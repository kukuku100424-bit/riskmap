from flask import Flask, request, jsonify, render_template_string, Response, send_file
import pandas as pd
import os
import re
import time
import threading
import webbrowser
from urllib.parse import quote

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_PATH = os.path.join(BASE_DIR, "mapdata_geocoded.xlsx")
DATA_CACHE = None

TYPE_COLORS = {
    "교통사고다발구역": "#ef4444",
    "상습결빙구역": "#06b6d4",
    "상습결빙지역": "#06b6d4",
    "상습침수구역": "#2563eb",
    "화재다빈도발생구역": "#f97316",
    "우범지역": "#7c3aed",
}

TYPE_ORDER = [
    "교통사고다발구역",
    "상습결빙구역",
    "상습침수구역",
    "화재다빈도발생구역",
    "우범지역",
]

DEFAULT_CENTER = [34.85, 126.90]
DEFAULT_ZOOM = 9

last_heartbeat = time.time()
shutdown_started = False


def safe_str(v):
    if pd.isna(v):
        return ""
    return str(v).strip()


def extract_town_from_address(address):
    addr = safe_str(address)
    if not addr:
        return "읍면동없음"

    # 읍/면/동/가/리 추출
    found = re.findall(r'([가-힣0-9]+(?:읍|면|동|가|리))', addr)
    if found:
        for token in reversed(found):
            if token.endswith(("읍", "면", "동", "가", "리")):
                return token

    return "읍면동없음"


def sample_desc(category, city, town, address):
    if category == "교통사고다발구역":
        return f"{city} {town} 일대 교차로·차량 진출입이 잦아 사고 위험이 높은 지역으로 가정한 샘플 설명입니다."
    if category in ["상습결빙구역", "상습결빙지역"]:
        return f"{city} {town} 일대는 겨울철 노면 결빙 우려가 높은 구간으로 가정한 샘플 설명입니다."
    if category == "상습침수구역":
        return f"{city} {town} 일대는 집중호우 시 배수 지연 및 도로 침수가 반복될 수 있는 구간으로 가정한 샘플 설명입니다."
    if category == "화재다빈도발생구역":
        return f"{city} {town} 일대는 화재 발생 빈도가 상대적으로 높다고 가정한 샘플 설명입니다."
    if category == "우범지역":
        return f"{city} {town} 일대는 야간 보행 시 주의가 필요한 구역으로 가정한 샘플 설명입니다."
    return f"{city} {town} 위험지역 샘플 설명입니다. 주소: {address}"


def sample_date(category):
    m = {
        "교통사고다발구역": "2025-11-12",
        "상습결빙구역": "2025-12-28",
        "상습결빙지역": "2025-12-28",
        "상습침수구역": "2025-07-18",
        "화재다빈도발생구역": "2025-10-03",
        "우범지역": "2025-09-21",
    }
    return m.get(category, "2025-01-01")


def build_photo_url(row):
    raw = safe_str(row.get("사진URL", ""))
    if raw:
        return raw

    category = quote(safe_str(row.get("구분", "")))
    city = quote(safe_str(row.get("시군구", "")))
    town = quote(safe_str(row.get("읍면동", "")))
    return f"/sample-image?category={category}&city={city}&town={town}"


def load_df():

    global DATA_CACHE

    if DATA_CACHE is not None:
        return DATA_CACHE

    if not os.path.exists(FILE_PATH):
        raise FileNotFoundError(f"{FILE_PATH} 파일이 없습니다.")

    df = pd.read_excel(FILE_PATH)

    required = ["순번", "구분", "시군구", "주소", "위도", "경도"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"엑셀에 '{col}' 열이 없습니다.")

    if "읍면동" not in df.columns:
        df["읍면동"] = df["주소"].apply(extract_town_from_address)

    if "사고설명" not in df.columns:
        df["사고설명"] = ""

    if "날짜" not in df.columns:
        df["날짜"] = ""

    if "사진URL" not in df.columns:
        df["사진URL"] = ""

    for col in ["구분", "시군구", "주소", "읍면동", "사고설명", "날짜", "사진URL"]:
        df[col] = df[col].apply(safe_str)

    df["위도"] = pd.to_numeric(df["위도"], errors="coerce")
    df["경도"] = pd.to_numeric(df["경도"], errors="coerce")

    # 좌표 없는 건 자동 제외
    df = df[df["위도"].notna() & df["경도"].notna()].copy()

    DATA_CACHE = df
    return DATA_CACHE


def row_to_dict(row):
    category = safe_str(row["구분"])
    city = safe_str(row["시군구"])
    town = safe_str(row["읍면동"])
    address = safe_str(row["주소"])

    desc = safe_str(row.get("사고설명", ""))
    if not desc:
        desc = sample_desc(category, city, town, address)

    date_value = safe_str(row.get("날짜", ""))
    if not date_value:
        date_value = sample_date(category)

    return {
        "순번": safe_str(row["순번"]),
        "구분": category,
        "시군구": city,
        "읍면동": town,
        "주소": address,
        "위도": float(row["위도"]),
        "경도": float(row["경도"]),
        "사고설명": desc,
        "날짜": date_value,
        "사진URL": build_photo_url(row),
        "마커색상": TYPE_COLORS.get(category, "#334155"),
    }


HTML = r"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>위험지역 찾기</title>

<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>

<style>
*{box-sizing:border-box}
html,body{
  margin:0;
  padding:0;
  width:100%;
  height:100%;
  font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif;
  background:#f8fafc;
  color:#0f172a;
}
.page{
  display:flex;
  width:100%;
  height:100vh;
  overflow:hidden;
}
.sidebar{
  width:360px;
  min-width:360px;
  background:#fff;
  border-right:1px solid #e5e7eb;
  padding:18px 16px;
  overflow-y:auto;
}
.brand{
  display:flex;
  align-items:center;
  gap:14px;
  margin-bottom:18px;
}

.brand-left{
  display:flex;
  flex-direction:column;
}

.brand-title{
  display:flex;
  align-items:center;
  justify-content:center;
  gap:10px;

  font-size:22px;
  font-weight:900;
  margin:0;
  line-height:1.2;
}

.brand-sub{
  font-size:13px;
  color:#64748b;
  margin-top:3px;
}

.ci-logo{
  height:48px;
  object-fit:contain;
  margin-left:8px;
}

@media (max-width:900px){
  .ci-logo{
    height:44px;
  }
}

.logo{
  width:48px;
  height:48px;
  border-radius:16px;
  display:flex;
  align-items:center;
  justify-content:center;
  color:#fff;
  font-size:24px;
  background:linear-gradient(135deg,#2563eb,#7c3aed);
  box-shadow:0 10px 24px rgba(37,99,235,.25);
}
.brand h1{
  margin:0;
  font-size:22px;
  line-height:1.2;
}
.brand p{
  margin:4px 0 0 0;
  font-size:13px;
  color:#64748b;
}
.card{
  background:#f8fafc;
  border:1px solid #e2e8f0;
  border-radius:18px;
  padding:15px;
  margin-bottom:14px;
}
.card h3{
  margin:0 0 12px 0;
  font-size:15px;
}
.label{
  display:block;
  margin:10px 0 6px;
  font-size:13px;
  font-weight:700;
  color:#334155;
}
.select, .btn{
  width:100%;
  min-height:46px;
  border-radius:12px;
  border:1px solid #cbd5e1;
  font-size:15px;
}
.select{
  background:#fff;
  padding:0 12px;
}
.check-grid{
  display:grid;
  grid-template-columns:1fr;
  gap:8px;
}
.check-item{
  display:flex;
  align-items:center;
  gap:10px;
  padding:10px 12px;
  border:1px solid #e2e8f0;
  background:#fff;
  border-radius:12px;
  font-size:14px;
}
.dot{
  width:12px;
  height:12px;
  border-radius:999px;
  flex:0 0 12px;
}
.btn-row{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:10px;
  margin-top:14px;
}
.btn{
  cursor:pointer;
  font-weight:800;
}
.btn.primary{
  background:#2563eb;
  color:#fff;
  border:none;
}
.btn.secondary{
  background:#fff;
  color:#111827;
}
.summary{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:10px;
}
.summary-box{
  background:#fff;
  border:1px solid #e2e8f0;
  border-radius:14px;
  padding:14px;
}
.summary-box .num{
  font-size:24px;
  font-weight:900;
  margin-bottom:4px;
}
.summary-box .txt{
  font-size:12px;
  color:#64748b;
}


@media (max-width:900px){

.legend{
display:none;
}

}

.legend-item{
  display:flex;
  align-items:center;
  gap:8px;
  font-size:13px;
}
.notice{
  font-size:12px;
  line-height:1.6;
  color:#475569;
}
.map-wrap{
  position:relative;
  flex:1;
  min-width:0;
}
#map{
  width:100%;
  height:100vh;
}

.top-badge{
  position:absolute;
  top:14px;
  right:14px;
  z-index:999;
  background:rgba(255,255,255,.96);
  border:1px solid #e5e7eb;
  border-radius:14px;
  padding:10px 12px;
  font-size:13px;
  box-shadow:0 8px 24px rgba(15,23,42,.08);
}

.map-legend{
  position:absolute;
  top:70px;
  right:14px;
  z-index:999;
  background:rgba(255,255,255,.96);
  border:1px solid #e5e7eb;
  border-radius:12px;
  padding:10px 12px;
  font-size:12px;
  line-height:1.6;
  box-shadow:0 8px 24px rgba(15,23,42,.08);
}

.map-legend-item{
  display:flex;
  align-items:center;
  gap:6px;
  margin-bottom:4px;
}

.map-legend-dot{
  width:18px;
  height:18px;
  border-radius:50%;
  flex-shrink:0;
  display:inline-block;
}
.loading{
  position:absolute;
  left:50%;
  top:50%;
  transform:translate(-50%,-50%);
  z-index:1000;
  background:rgba(15,23,42,.86);
  color:#fff;
  padding:12px 16px;
  border-radius:14px;
  font-size:14px;
  display:none;
}
.custom-marker{
  width:18px;
  height:18px;
  border-radius:999px;
  border:3px solid #fff;
  box-shadow:0 2px 8px rgba(0,0,0,.25);
}
.popup-wrap{
  width:245px;
}
.popup-img{
  width:100%;
  height:136px;
  object-fit:cover;
  border-radius:12px;
  border:1px solid #e5e7eb;
  margin-bottom:10px;
  background:#f1f5f9;
}
.popup-title{
  font-size:16px;
  font-weight:900;
  margin-bottom:6px;
  line-height:1.35;
}
.popup-meta{
  font-size:12px;
  color:#64748b;
  line-height:1.5;
  margin-bottom:8px;
}
.popup-desc{
  font-size:13px;
  line-height:1.55;
}
@media (max-width: 900px){
  .page{
    display:block;
    height:auto;
    min-height:100vh;
  }

  .sidebar{
    width:100%;
    min-width:0;
    border-right:none;
    border-bottom:none;
    padding:15px 14px;
  }

  .map-wrap{
    display:none;
  }

  .legend{
    grid-template-columns:1fr;
  }
}


.mobile-map-popup{
  position:fixed;
  inset:0;
  background:#ffffff;
  z-index:2000;
  display:none;
  flex-direction:column;
}

.mobile-map-header{
  height:56px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:0 16px;
  border-bottom:1px solid #e5e7eb;
  font-weight:700;
}

.mobile-map-close{
  border:none;
  background:#ef4444;
  color:#ffffff;
  padding:6px 10px;
  border-radius:6px;
}

@media (min-width:901px){

  .mobile-map-popup{
    display:none !important;
  }

}

.mobile-map{
  flex:1;
}

@media(min-width:901px){
  .mobile-map-popup{
    display:none !important;
  }
}

#locBtn{
position:absolute;
bottom:20px;
right:20px;
z-index:1000;

width:46px;
height:46px;

border-radius:50%;
border:none;

background:#ffffff;
color:#2563eb;

font-size:20px;

display:flex;
align-items:center;
justify-content:center;

box-shadow:0 6px 16px rgba(0,0,0,0.25);

cursor:pointer;
}


@media (max-width:900px){

.map-legend{
  top:auto;
  bottom:80px;
  right:10px;
  font-size:11px;
  padding:8px;
}

.map-legend-dot{
  width:16px;
  height:16px;
}

}
.mobile-map-popup .map-legend{
  position:absolute;
  top:70px;
  right:6px;
  bottom:auto;
  z-index:3000;
}

.user-marker-wrap{
  position:relative;
  width:28px;
  height:28px;
}

.user-pin{
  position:relative;
  width:34px;
  height:34px;
}

.user-pin::before{
  content:"";
  position:absolute;
  left:50%;
  top:50%;
  width:18px;
  height:18px;
  background:#22c55e;
  border-radius:50%;
  border:3px solid #ffffff;
  transform:translate(-50%,-50%);
  box-shadow:0 4px 12px rgba(0,0,0,.35);
}

.user-pin::after{
  content:"";
  position:absolute;
  left:50%;
  top:50%;
  width:34px;
  height:34px;
  border-radius:50%;
  background:rgba(34,197,94,0.25);
  transform:translate(-50%,-50%);
  animation:userPulse 1.8s infinite;
}

.user-marker-pulse{
  position:absolute;
  left:50%;
  top:50%;
  width:28px;
  height:28px;
  transform:translate(-50%,-50%);
  border-radius:50%;
  background:rgba(37,99,235,0.22);
  animation:userPulse 1.8s ease-out infinite;
}

.user-marker-dot{
  position:absolute;
  left:50%;
  top:50%;
  width:14px;
  height:14px;
  transform:translate(-50%,-50%);
  border-radius:50%;
  background:#2563eb;
  border:3px solid #ffffff;
  box-shadow:0 2px 8px rgba(0,0,0,.25);
}

@keyframes userPulse{
  0%{
    transform:translate(-50%,-50%) scale(0.7);
    opacity:0.9;
  }
  100%{
    transform:translate(-50%,-50%) scale(1.8);
    opacity:0;
  }
}

.location-box{
  margin-top:12px;
  padding:12px;
  border:1px solid #e2e8f0;
  border-radius:16px;
  background:#f8fafc;
}

.location-row{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:8px;
  margin-top:8px;
}

.location-box .btn{
  background:#ffffff;
  color:#111827;
  border:1px solid #cbd5e1;
}

.mobile-result-panel{
  position:fixed;
  bottom:0;
  left:0;
  right:0;
  height:220px;
  background:white;
  border-top-left-radius:16px;
  border-top-right-radius:16px;
  box-shadow:0 -6px 20px rgba(0,0,0,.15);
  z-index:3000;
  display:none;
  flex-direction:column;
}

.mobile-result-header{
  padding:10px 14px;
  font-weight:700;
  border-bottom:1px solid #e5e7eb;
}

.mobile-result-list{
  overflow:auto;
  flex:1;
}

.mobile-result-item{
  padding:10px 14px;
  border-bottom:1px solid #f1f5f9;
  font-size:13px;
  cursor:pointer;
}

.mobile-result-item:hover{
  background:#f8fafc;
}

.mobile-result-distance{
  font-size:12px;
  color:#2563eb;
}

#mobileLocBtn{
position:absolute;
bottom:20px;
right:20px;
z-index:4000;

width:52px;
height:52px;

border-radius:50%;
border:none;

background:#2563eb;
color:white;

font-size:22px;

display:flex;
align-items:center;
justify-content:center;

box-shadow:0 6px 16px rgba(0,0,0,0.25);

cursor:pointer;
}


</style>
</head>
<body>

<div class="page">
  <aside class="sidebar">
<div class="brand">

  <div class="brand-left">
    <div class="brand-title">
      <span>위험지역 찾기</span>
      <img src="/ci" class="ci-logo">
    </div>

    <div class="brand-sub">엑셀 기반 위험정보 지도</div>
  </div>

</div>    <div class="card">
      <h3>조회 조건</h3>

      <label class="label">1. 시군구</label>
      <select id="city" class="select">
        <option value="">전체</option>
      </select>

      <label class="label">2. 읍면동</label>
      <select id="town" class="select">
        <option value="">전체</option>
      </select>

      <label class="label">3. 구분</label>
      <div class="check-grid" id="categoryBox"></div>

            <div class="btn-row">
        <button class="btn primary" onclick="loadData()">조회</button>
        <button class="btn secondary" onclick="resetFilters()">필터 초기화</button>
      </div>

<div class="location-box">

<button class="btn secondary" onclick="findNearest()">
내 주변 위험지역 찾기
</button>

<div class="location-row">

<button class="btn secondary" onclick="findRadius(1)">
1km 위험지역
</button>

<button class="btn secondary" onclick="findRadius(3)">
3km 위험지역
</button>

</div>

</div>
</div>

<div class="card">
      <h3>조회 결과</h3>
      <div class="summary">
        <div class="summary-box">
          <div class="num" id="countTotal">0</div>
          <div class="txt">표시된 지점 수</div>
        </div>
        <div class="summary-box">
          <div class="num" id="countCity">전체</div>
          <div class="txt">현재 시군구</div>
        </div>
      </div>
    </div>

    <div class="card">
        <div class="legend">
        <div class="legend-item"><span class="dot" style="background:#ef4444"></span>교통사고다발구역</div>
        <div class="legend-item"><span class="dot" style="background:#06b6d4"></span>상습결빙구역</div>
        <div class="legend-item"><span class="dot" style="background:#2563eb"></span>상습침수구역</div>
        <div class="legend-item"><span class="dot" style="background:#f97316"></span>화재다빈도발생구역</div>
        <div class="legend-item"><span class="dot" style="background:#7c3aed"></span>우범지역</div>
      </div>
    </div>

  </aside>

  <main class="map-wrap">
    <div id="map"></div>
    <button id="locBtn">📍 내 위치</button>
    <div class="top-badge">모바일 / PC 지원</div>

<div class="map-legend">

  <div class="map-legend-item">
    <span class="map-legend-dot" style="background:#ef4444"></span>
    교통사고다발구역
  </div>

  <div class="map-legend-item">
    <span class="map-legend-dot" style="background:#06b6d4"></span>
    상습결빙구역
  </div>

  <div class="map-legend-item">
    <span class="map-legend-dot" style="background:#2563eb"></span>
    상습침수구역
  </div>

  <div class="map-legend-item">
    <span class="map-legend-dot" style="background:#f97316"></span>
    화재다빈도발생구역
  </div>

  <div class="map-legend-item">
    <span class="map-legend-dot" style="background:#7c3aed"></span>
    우범지역
  </div>

<div class="map-legend-item">
  <span class="map-legend-dot" style="background:#22c55e"></span>
내 위치
</div>

</div>


    <div class="loading" id="loadingBox">데이터를 불러오는 중입니다...</div>
  </main>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>

<script>
const CATEGORY_COLORS = {
  "교통사고다발구역": "#ef4444",
  "상습결빙구역": "#06b6d4",
  "상습결빙지역": "#06b6d4",
  "상습침수구역": "#2563eb",
  "화재다빈도발생구역": "#f97316",
  "우범지역": "#7c3aed"
};

const CATEGORY_LIST = [
  "교통사고다발구역",
  "상습결빙구역",
  "상습침수구역",
  "화재다빈도발생구역",
  "우범지역"
];

const map = L.map("map", { zoomControl:true }).setView([34.85, 126.90], 9);

let userLat = null;
let userLng = null;


function showMsg(text){
  document.getElementById("msgText").innerText = text;
  document.getElementById("msgModal").style.display = "flex";
}

function closeMsg(){
  document.getElementById("msgModal").style.display = "none";
}

function preloadLocation(){
  if(!navigator.geolocation){
    return;
  }

  navigator.geolocation.getCurrentPosition(

    function(pos){

      userLat = pos.coords.latitude;
      userLng = pos.coords.longitude;

    },

    function(err){
      console.log("위치 사전 요청 실패", err);
    },

    {
      enableHighAccuracy:false,
      timeout:4000,
      maximumAge:60000
    }

  );

}


L.tileLayer(
  "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap"
  }
).addTo(map);

let markerGroup = L.markerClusterGroup({
  showCoverageOnHover:false,
  spiderfyOnMaxZoom:true,
  disableClusteringAtZoom:15
});
map.addLayer(markerGroup);

function setLoading(show){
  document.getElementById("loadingBox").style.display = show ? "block" : "none";
}

function calcDistance(lat1, lng1, lat2, lng2){

  const R = 6371000;

  const dLat = (lat2-lat1) * Math.PI/180;
  const dLng = (lng2-lng1) * Math.PI/180;

  const a =
    Math.sin(dLat/2)*Math.sin(dLat/2) +
    Math.cos(lat1*Math.PI/180) *
    Math.cos(lat2*Math.PI/180) *
    Math.sin(dLng/2)*Math.sin(dLng/2);

  const c = 2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));

  return R*c;

}

function showMobileResults(items, userLat, userLng){
history.pushState({mobileResult:true}, "");
  const panel = document.getElementById("mobileResultPanel");
  const list = document.getElementById("mobileResultList");

  panel.style.display = "flex";

  list.innerHTML = "";

  items.forEach(item=>{

    const dist = Math.round(
      calcDistance(userLat,userLng,item.위도,item.경도)
    );

    const el = document.createElement("div");

    el.className = "mobile-result-item";

    el.innerHTML = `
      <b>${item.구분}</b><br>
      ${item.시군구} ${item.읍면동}<br>
      <span class="mobile-result-distance">${dist} m</span>
    `;

    el.onclick = function(){

      window.mobileLeafletMap.setView(
        [item.위도,item.경도],16
      );

    };

    list.appendChild(el);

  });

  document.getElementById("mobileResultCount").textContent = items.length;

}


function createCategoryChecks(){
  const box = document.getElementById("categoryBox");
  box.innerHTML = "";
  CATEGORY_LIST.forEach(cat => {
    const color = CATEGORY_COLORS[cat] || "#334155";
    const item = document.createElement("label");
    item.className = "check-item";
    item.innerHTML = `
      <input type="checkbox" class="category-check" value="${cat}">
      <span class="dot" style="background:${color}"></span>
      <span>${cat}</span>
    `;
    box.appendChild(item);
  });
}

function fillCities(cities){
  const select = document.getElementById("city");
  const current = select.value;
  select.innerHTML = '<option value="">전체</option>';
  cities.forEach(city => {
    const op = document.createElement("option");
    op.value = city;
    op.textContent = city;
    select.appendChild(op);
  });
  if(cities.includes(current)) select.value = current;
}

function fillTowns(towns){
  const select = document.getElementById("town");
  const current = select.value;
  select.innerHTML = '<option value="">전체</option>';
  towns.forEach(town => {
    const op = document.createElement("option");
    op.value = town;
    op.textContent = town;
    select.appendChild(op);
  });
  if(towns.includes(current)) select.value = current;
}

function getCheckedCategories(){
  return Array.from(document.querySelectorAll(".category-check:checked")).map(el => el.value);
}

function buildMarkerIcon(color){
  return L.divIcon({
    className: "",
    html: `<div class="custom-marker" style="background:${color};"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    popupAnchor: [0, -8]
  });
}

function buildUserIcon(){
  return L.divIcon({
    className: "",
    html: `
      <div class="user-pin"></div>
    `,
    iconSize: [34,34],
    iconAnchor: [17,17]
  });
} 

function escapeHtml(text){
  if(text === null || text === undefined) return "";
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadMeta(){
  const res = await fetch("/meta");
  const data = await res.json();
  fillCities(data.cities || []);
  fillTowns(data.towns || []);
}

async function updateTowns(){
  const city = document.getElementById("city").value;
  const res = await fetch(`/towns?city=${encodeURIComponent(city)}`);
  const data = await res.json();
  fillTowns(data.towns || []);
}

async function loadData(){

  if(isMobile()){
    openMobileMap();
  }

  setLoading(true);

  if(!isMobile()){
    markerGroup.clearLayers();
  }


  const city = document.getElementById("city").value;
  const town = document.getElementById("town").value;
  const categories = getCheckedCategories();

  const params = new URLSearchParams();
  if(city) params.append("city", city);
  if(town) params.append("town", town);
  categories.forEach(cat => params.append("category", cat));

  try{
    const res = await fetch(`/data?${params.toString()}`);
    const data = await res.json();

    document.getElementById("countTotal").textContent = data.length;
    document.getElementById("countCity").textContent = city || "전체";

    const bounds = [];

    data.forEach(item => {

  if(!isMobile()){

    const icon = buildMarkerIcon(item.마커색상);
    const marker = L.marker([item.위도, item.경도], { icon });

      const popupHtml = `
        <div class="popup-wrap">

          <img class="popup-img" src="${escapeHtml(item.사진URL)}" alt="현장 사진">

          <div class="popup-title">${escapeHtml(item.구분)}</div>

          <div class="popup-meta">
            순번: ${escapeHtml(item.순번)}<br>
            시군구: ${escapeHtml(item.시군구)}<br>
            읍면동: ${escapeHtml(item.읍면동)}<br>
            날짜: ${escapeHtml(item.날짜)}<br>
            주소: ${escapeHtml(item.주소)}
          </div>

          <div class="popup-desc">
            ${escapeHtml(item.사고설명)}
          </div>

          <div style="margin-top:10px;">
            <a 
              href="https://map.naver.com/v5/search/${encodeURIComponent(item.주소)}"
              target="_blank"
              style="
                display:block;
                text-align:center;
                background:#03C75A;
                color:#ffffff;
                font-weight:700;
                padding:8px;
                border-radius:8px;
                text-decoration:none;
                font-size:13px;
              ">
              네이버지도 길찾기
            </a>
          </div>

                  </div>
      `;

      marker.bindPopup(popupHtml, { maxWidth: 290 });
      markerGroup.addLayer(marker);
bounds.push([item.위도, item.경도]);

}

});

if(!isMobile()){

  if(bounds.length > 0){
    map.fitBounds(bounds, { padding:[40,40] });
  }else{
    map.setView([34.85, 126.90], 9);
    showMsg("조건에 맞는 데이터가 없습니다.");
  }

}

if(isMobile()){
  syncToMobileMap(data);
}

  }catch(e){
    showMsg("데이터를 불러오는 중 오류가 발생했습니다.");
    console.error(e);


  }finally{
    setLoading(false);
  }
}

function resetFilters(){

  // 시군구 초기화
  document.getElementById("city").value = "";

  // 읍면동 초기화
  document.getElementById("town").value = "";

  // 구분 체크 해제
  document.querySelectorAll(".category-check")
  .forEach(el => el.checked = false);

  // 결과 숫자 초기화
  document.getElementById("countTotal").textContent = 0;
  document.getElementById("countCity").textContent = "전체";

  // PC 지도 마커 삭제
  if(markerGroup){
    markerGroup.clearLayers();
  }

  // 모바일 지도 마커 삭제
  if(window.mobileMarkerGroup){
    window.mobileMarkerGroup.clearLayers();
  }

  // 지도 위치 초기화
  map.setView([34.85,126.90],9);

    // 모바일 결과 패널 닫기
  const result = document.getElementById("mobileResultPanel");
  if(result){
    result.style.display = "none";
  }

  // 데이터 다시 불러오기
  loadData();

}
window.addEventListener("load", function(){
  setTimeout(()=>{
    map.invalidateSize();
  },500);
});

window.addEventListener("DOMContentLoaded", function(){

  preloadLocation();

  createCategoryChecks();

  loadMeta().then(()=>{
    document.getElementById("city").addEventListener("change", updateTowns);

    if(!isMobile()){
      loadData();
    }

  });

});

// ===== 브라우저 닫힘 감지용 heartbeat =====
function heartbeat(){
  fetch("/heartbeat", {method:"POST"}).catch(()=>{});
}
setInterval(heartbeat, 5000);
heartbeat();

window.addEventListener("beforeunload", function(){
  navigator.sendBeacon("/bye");
});



function isMobile(){
  return window.innerWidth < 900;
}

function openMobileMap(){

  if(!history.state || !history.state.mobileMap){
    history.pushState({mobileMap:true}, "");
  }

  if(!isMobile()) return;

  const popup = document.getElementById("mobileMapPopup");
  popup.style.display = "flex";

  const mapDiv = document.getElementById("mobileMap");

  if(!window.mobileLeafletMap){

    

    window.mobileLeafletMap = L.map(mapDiv).setView([34.85, 126.90], 9);

    L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 19 }
    ).addTo(window.mobileLeafletMap);

    window.mobileMarkerGroup = L.markerClusterGroup({
      showCoverageOnHover:false,
      spiderfyOnMaxZoom:true,
      disableClusteringAtZoom:15
    });

    window.mobileLeafletMap.addLayer(window.mobileMarkerGroup);
  }

  setTimeout(()=>{
    window.mobileLeafletMap.invalidateSize();
  }, 200);
}

function closeMobileMap(){

  document.getElementById("mobileMapPopup").style.display = "none";

  const result = document.getElementById("mobileResultPanel");

  if(result){
    result.style.display = "none";
  }

}


function syncToMobileMap(items, userLat=null, userLng=null, radiusMeter=null){

  if(!isMobile()) return;

  if(userLat && userLng){

    items.sort((a,b)=>{

      const da = calcDistance(userLat,userLng,a.위도,a.경도);
      const db = calcDistance(userLat,userLng,b.위도,b.경도);

      return da-db;

    });

  }




  openMobileMap();

  if(window.mobileMarkerGroup){
  window.mobileMarkerGroup.clearLayers();
}

  const bounds = [];

  if(userLat !== null && userLng !== null){
    const userMarker = L.marker(
  [userLat, userLng],
  { icon: buildUserIcon() }
);

window.mobileMarkerGroup.addLayer(userMarker);
    bounds.push([userLat, userLng]);

    if(radiusMeter){
      const circle = L.circle(
        [userLat, userLng],
        {
          radius: radiusMeter,
          color:"#2563eb",
          fillColor:"#2563eb",
          fillOpacity:0.08
        }
      );
      window.mobileMarkerGroup.addLayer(circle);
    }
  }

  items.forEach(item=>{
    const icon = buildMarkerIcon(item.마커색상);

    const marker = L.marker([item.위도, item.경도], { icon });

    const popupHtml = `
      <div class="popup-wrap">
        <img class="popup-img" src="${escapeHtml(item.사진URL)}" alt="현장 사진">

        <div class="popup-title">${escapeHtml(item.구분)}</div>

        <div class="popup-meta">
          순번: ${escapeHtml(item.순번)}<br>
          시군구: ${escapeHtml(item.시군구)}<br>
          읍면동: ${escapeHtml(item.읍면동)}<br>
          날짜: ${escapeHtml(item.날짜)}<br>
          주소: ${escapeHtml(item.주소)}
        </div>

        <div class="popup-desc">
          ${escapeHtml(item.사고설명)}
        </div>

          <div style="margin-top:10px;">
            <a 
              href="https://map.naver.com/v5/search/${encodeURIComponent(item.주소)}"
              target="_blank"
              style="
                display:block;
                text-align:center;
                background:#03C75A;
                color:#ffffff;
                font-weight:700;
                padding:8px;
                border-radius:8px;
                text-decoration:none;
                font-size:13px;
              ">
              네이버지도 길찾기
            </a>
          </div>
        </div>
    `;

    marker.bindPopup(popupHtml, { maxWidth: 290 });
    window.mobileMarkerGroup.addLayer(marker);
    bounds.push([item.위도, item.경도]);
  });

  setTimeout(()=>{
    window.mobileLeafletMap.invalidateSize();

    if(bounds.length > 0){
      window.mobileLeafletMap.fitBounds(bounds, { padding:[40,40] });
    }else{
      window.mobileLeafletMap.setView([34.85, 126.90], 9);
    }
  }, 250);


// 🔵 여기 추가
if(userLat && userLng){
  showMobileResults(items,userLat,userLng);
}

}  


async function findNearest(){

  if(!navigator.geolocation){
    showMsg("GPS를 지원하지 않는 기기입니다.");
    return;
  }

  navigator.geolocation.getCurrentPosition(

  async pos => {

    const lat = pos.coords.latitude;
    const lng = pos.coords.longitude;

    userLat = lat;
    userLng = lng;

    const res = await fetch("/data");
    const data = await res.json();

    markerGroup.clearLayers();

    L.marker(
      [lat, lng],
      { icon: buildUserIcon() }
    ).addTo(markerGroup);

    let nearest = null;
    let minDist = Infinity;

    data.forEach(item => {

      const dist = map.distance(
        [lat, lng],
        [item.위도, item.경도]
      );

      if(dist < minDist){
        minDist = dist;
        nearest = item;
      }

    });

    if(!nearest){
      showMsg("주변 위험지역이 없습니다.");
      return;
    }

    const icon = buildMarkerIcon(nearest.마커색상);

    const marker = L.marker(
      [nearest.위도, nearest.경도],
      { icon }
    );

    const popupHtml = `
<div class="popup-wrap">

<img class="popup-img" src="${escapeHtml(nearest.사진URL)}" alt="현장 사진">

<div class="popup-title">${escapeHtml(nearest.구분)}</div>

<div class="popup-meta">
순번: ${escapeHtml(nearest.순번)}<br>
시군구: ${escapeHtml(nearest.시군구)}<br>
읍면동: ${escapeHtml(nearest.읍면동)}<br>
날짜: ${escapeHtml(nearest.날짜)}<br>
주소: ${escapeHtml(nearest.주소)}
</div>

<div class="popup-desc">
${escapeHtml(nearest.사고설명)}
</div>

<div style="margin-top:10px;">
<a 
href="https://map.naver.com/v5/search/${encodeURIComponent(nearest.주소)}"
target="_blank"
style="
display:block;
text-align:center;
background:#03C75A;
color:#ffffff;
font-weight:700;
padding:8px;
border-radius:8px;
text-decoration:none;
font-size:13px;
">
네이버지도 길찾기
</a>
</div>

</div>
`;


marker.bindPopup(popupHtml, { maxWidth: 290 });

    markerGroup.addLayer(marker);

    map.setView([nearest.위도, nearest.경도], 16);

    document.getElementById("countTotal").textContent = 1;
    document.getElementById("countCity").textContent = "내 주변";

    if(isMobile()){
      syncToMobileMap([nearest], lat, lng, null);
    }

  },
  err => {
    showMsg("위치를 가져올 수 없습니다.");
  },
  {
    enableHighAccuracy:false,
    timeout:5000,
    maximumAge:60000
  });

}

async function findRadius(km){

  if(!navigator.geolocation){
    showMsg("GPS를 지원하지 않는 기기입니다.");
    return;
  }

  navigator.geolocation.getCurrentPosition(
  async pos => {

    const lat = pos.coords.latitude;
    const lng = pos.coords.longitude;

    userLat = lat;
    userLng = lng;

    const res = await fetch("/data");
    const data = await res.json();

    markerGroup.clearLayers();

    const radiusMeter = km * 1000;

    L.marker(
      [lat, lng],
      { icon: buildUserIcon() }
    ).addTo(markerGroup);

    L.circle(
      [lat, lng],
      {
        radius: radiusMeter,
        color:"#2563eb",
        fillColor:"#2563eb",
        fillOpacity:0.08
      }
    ).addTo(markerGroup);

    const filtered = [];

    data.forEach(item => {

      const dist = map.distance(
        [lat, lng],
        [item.위도, item.경도]
      );

      if(dist <= radiusMeter){

        filtered.push(item);

        const icon = buildMarkerIcon(item.마커색상);

        const marker = L.marker(
          [item.위도, item.경도],
          { icon }
        );
const popupHtml = `
<div class="popup-wrap">

<img class="popup-img" src="${escapeHtml(item.사진URL)}" alt="현장 사진">

<div class="popup-title">${escapeHtml(item.구분)}</div>

<div class="popup-meta">
순번: ${escapeHtml(item.순번)}<br>
시군구: ${escapeHtml(item.시군구)}<br>
읍면동: ${escapeHtml(item.읍면동)}<br>
날짜: ${escapeHtml(item.날짜)}<br>
주소: ${escapeHtml(item.주소)}
</div>

<div class="popup-desc">
${escapeHtml(item.사고설명)}
</div>

<div style="margin-top:10px;">
<a 
href="https://map.naver.com/v5/search/${encodeURIComponent(item.주소)}"
target="_blank"
style="
display:block;
text-align:center;
background:#03C75A;
color:#ffffff;
font-weight:700;
padding:8px;
border-radius:8px;
text-decoration:none;
font-size:13px;
">
네이버지도 길찾기
</a>
</div>

</div>
`;

marker.bindPopup(popupHtml, { maxWidth: 290 });

        markerGroup.addLayer(marker);

      }

    });

    if(filtered.length === 0){

  showMsg(`${km}km 안에 위험지역이 없습니다.`);

  return;
}

    const bounds = [
      [lat, lng],
      ...filtered.map(item => [item.위도, item.경도])
    ];

    document.getElementById("countTotal").textContent = filtered.length;
    document.getElementById("countCity").textContent = "내 주변";

    map.fitBounds(bounds, { padding:[40,40] });

    if(isMobile()){
      syncToMobileMap(filtered, lat, lng, radiusMeter);
    }

  },
  err => {
    showMsg("위치를 가져올 수 없습니다.");
  },
  {
    enableHighAccuracy:false,
    timeout:5000,
    maximumAge:60000
  });

}

window.addEventListener("DOMContentLoaded", function(){

  const locBtn = document.getElementById("locBtn");

  if(locBtn){
    locBtn.onclick = function(){

      if(!navigator.geolocation){
        showMsg("GPS를 지원하지 않는 기기입니다.");
        return;
      }

navigator.geolocation.getCurrentPosition(

pos=>{

const lat = pos.coords.latitude;
const lng = pos.coords.longitude;

markerGroup.clearLayers();

map.setView([lat, lng], 15);

L.marker(
  [lat, lng],
  { icon: buildUserIcon() }
).addTo(markerGroup);

if(window.mobileLeafletMap){
  window.mobileLeafletMap.setView([lat,lng],15);
}

if(window.mobileMarkerGroup){
  window.mobileMarkerGroup.clearLayers();

  L.marker([lat,lng],{icon:buildUserIcon()})
  .addTo(window.mobileMarkerGroup);
}


},

err=>{
  showMsg("GPS를 지원하지 않는 기기입니다.");
},

{
  enableHighAccuracy:false,
  timeout:4000,
  maximumAge:60000
}

);
    };
  }

});
window.addEventListener("popstate", function(){

const popup = document.getElementById("mobileMapPopup");
const result = document.getElementById("mobileResultPanel");

if(result && result.style.display === "flex"){
  result.style.display = "none";
  return;
}

if(popup && popup.style.display === "flex"){
  popup.style.display = "none";
  return;
}

});

window.addEventListener("DOMContentLoaded", function(){

const mobileBtn = document.getElementById("mobileLocBtn");

if(mobileBtn){

mobileBtn.onclick = function(){

if(!navigator.geolocation){
  alert("GPS를 지원하지 않습니다.");
  return;
}

navigator.geolocation.getCurrentPosition(pos=>{
const lat = pos.coords.latitude;
const lng = pos.coords.longitude;

if(window.mobileLeafletMap){

  window.mobileLeafletMap.setView([lat,lng],15);

  if(window.mobileMarkerGroup){
    window.mobileMarkerGroup.clearLayers();

    L.marker([lat,lng],{icon:buildUserIcon()})
    .addTo(window.mobileMarkerGroup);
  }

}});

};

}

});


</script>

<div class="mobile-result-panel" id="mobileResultPanel">
  <div class="mobile-result-header">
    검색 결과 <span id="mobileResultCount">0</span>건
  </div>
  <div class="mobile-result-list" id="mobileResultList"></div>
</div>

<div class="mobile-map-popup" id="mobileMapPopup">

  <div class="mobile-map-header">
    지도 보기
    <button class="mobile-map-close" onclick="closeMobileMap()">닫기</button>
  </div>

  <div id="mobileMap" class="mobile-map"></div>
  <button id="mobileLocBtn">📍</button>

  <div class="map-legend">

    <div class="map-legend-item">
      <span class="map-legend-dot" style="background:#ef4444"></span>
      교통사고다발구역
    </div>

    <div class="map-legend-item">
      <span class="map-legend-dot" style="background:#06b6d4"></span>
      상습결빙구역
    </div>

    <div class="map-legend-item">
      <span class="map-legend-dot" style="background:#2563eb"></span>
      상습침수구역
    </div>

    <div class="map-legend-item">
      <span class="map-legend-dot" style="background:#f97316"></span>
      화재다빈도발생구역
    </div>

    <div class="map-legend-item">
      <span class="map-legend-dot" style="background:#7c3aed"></span>
      우범지역
    </div>
<div class="map-legend-item">
  <span class="map-legend-dot" style="background:#22c55e"></span>
  내 위치
</div>


  </div>

</div>



<div id="msgModal" style="
position:fixed;
inset:0;
background:rgba(0,0,0,0.45);
display:none;
align-items:center;
justify-content:center;
z-index:5000;
">

<div style="
background:white;
padding:22px;
border-radius:14px;
width:280px;
text-align:center;
box-shadow:0 10px 30px rgba(0,0,0,.25);
">

<div id="msgText" style="
font-size:15px;
margin-bottom:18px;
line-height:1.5;
"></div>

<button onclick="closeMsg()" style="
background:#2563eb;
border:none;
color:white;
padding:8px 16px;
border-radius:8px;
font-weight:700;
cursor:pointer;
">
확인
</button>

</div>
</div>

</body>
</html>
"""


@app.route("/ci")
def ci():
    return send_file("ci.png", mimetype="image/png")

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/meta")
def meta():
    df = load_df()

    cities = sorted(df["시군구"].dropna().astype(str).unique().tolist())
    towns = sorted(df["읍면동"].dropna().astype(str).unique().tolist())

    return jsonify({
        "cities": cities,
        "towns": towns
    })


@app.route("/towns")
def towns():
    city = safe_str(request.args.get("city", ""))
    df = load_df()

    if city:
        df = df[df["시군구"] == city]

    town_list = sorted(df["읍면동"].dropna().astype(str).unique().tolist())
    return jsonify({"towns": town_list})


@app.route("/data")
def data():
    city = safe_str(request.args.get("city", ""))
    town = safe_str(request.args.get("town", ""))
    categories = request.args.getlist("category")

    df = load_df()

    if city:
        df = df[df["시군구"] == city]

    if town:
        df = df[df["읍면동"] == town]

    if categories:
        df = df[df["구분"].isin(categories)]

    records = [row_to_dict(row) for _, row in df.iterrows()]
    return jsonify(records)


@app.route("/sample-image")
def sample_image():
    category = safe_str(request.args.get("category", "위험지역"))
    city = safe_str(request.args.get("city", ""))
    town = safe_str(request.args.get("town", ""))

    color = TYPE_COLORS.get(category, "#334155")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450">
    <defs>
      <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="{color}"/>
        <stop offset="100%" stop-color="#0f172a"/>
      </linearGradient>
    </defs>
    <rect width="800" height="450" fill="url(#g)"/>
    <rect x="30" y="30" width="740" height="390" rx="24" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.2)"/>
    <text x="50%" y="42%" text-anchor="middle" fill="white" font-size="42" font-family="Arial" font-weight="700">{category}</text>
    <text x="50%" y="54%" text-anchor="middle" fill="white" font-size="24" font-family="Arial">{city} {town}</text>
    <text x="50%" y="70%" text-anchor="middle" fill="white" font-size="18" font-family="Arial">샘플 이미지</text>
    </svg>"""
    return Response(svg, mimetype="image/svg+xml")


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    global last_heartbeat
    last_heartbeat = time.time()
    return ("", 204)


@app.route("/bye", methods=["POST"])
def bye():
    global last_heartbeat
    last_heartbeat = 0
    return ("", 204)


def shutdown_watcher():
    global shutdown_started
    while True:
        time.sleep(3)
        if shutdown_started:
            return

        # 브라우저 닫힘 이후 10초 내 서버 종료
        if time.time() - last_heartbeat > 10:
            shutdown_started = True
            print("브라우저 연결이 종료되어 서버를 닫습니다.")
            os._exit(0)


def open_preferred_browser(url):
    time.sleep(1.5)

    candidates = [
        r"C:\Program Files\Naver\Naver Whale\Application\whale.exe",
        r"C:\Program Files (x86)\Naver\Naver Whale\Application\whale.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]

    for path in candidates:
        if os.path.exists(path):
            try:
                webbrowser.register("preferred_browser", None, webbrowser.BackgroundBrowser(path))
                webbrowser.get("preferred_browser").open(url)
                return
            except Exception:
                pass

    webbrowser.open(url)


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))
    url = f"http://127.0.0.1:{port}"

    # 로컬 실행일 때만 브라우저 자동 열기
    if os.environ.get("RENDER_SERVICE_ID") is None:
        threading.Thread(target=shutdown_watcher, daemon=True).start()
        threading.Thread(target=open_preferred_browser, args=(url,), daemon=True).start()

    app.run(host="0.0.0.0", port=port, debug=False)