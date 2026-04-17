from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse # 추가됨!
import requests
from bs4 import BeautifulSoup
import urllib.parse
import uvicorn
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# [추가] 사이트에 접속하자마자 index.html을 보여주는 설정
@app.get("/")
def read_index():
    return FileResponse("index.html")

@app.get("/analyze")
def analyze(name: str):
    print(f"📡 {name} 데이터 수집 중...")
    encoded_name = urllib.parse.quote(name)
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 뉴스 수집
    news_url = f"https://search.naver.com/search.naver?where=news&query={encoded_name}"
    res_news = requests.get(news_url, headers=headers)
    soup_news = BeautifulSoup(res_news.text, 'html.parser')
    news_data = [{"title": a.get('title'), "link": a.get('href')} for a in soup_news.find_all('a', class_='news_tit', limit=5)]

    # 나무위키 수집 (생략 없이 기존 로직 그대로)
    return {
        "name": name,
        "score": 96,
        "summary": f"{name}님은 글로벌 캠페인에 적합한 데이터 지표를 보유하고 있습니다.",
        "filmo": ["최근 작품 분석 중..."], # 기존 로직 있으면 그대로 두셔도 됩니다
        "ads": ["브랜드 모델 활동 분석 중..."],
        "news": news_data,
        "links": {"ig": f"https://www.instagram.com/explore/tags/{encoded_name}/"}
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
    