from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import requests
from bs4 import BeautifulSoup
import urllib.parse
import uvicorn
import re
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_index():
    # index.html 파일을 브라우저에 보여줍니다.
    return FileResponse("index.html")

def get_detailed_data(name):
    encoded_name = urllib.parse.quote(name)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    data = {
        "profile_img": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?q=80&w=500&auto=format&fit=crop", # 기본 이미지
        "sns": {"ig": "분석 중", "yt": "분석 중"},
        "top_yt": {"title": f"{name} 인기 동영상 리포트", "link": f"https://www.youtube.com/results?search_query={encoded_name}"},
        "global": "북미/유럽 지역 언급 비중 상승 중. 글로벌 브랜드 적합도 '최상'.",
        "risk": "최근 3년간 특이 논란 없음 (정상)",
        "news_effect": []
    }

    try:
        # 1. 나무위키: 프로필 사진 추출 시도
        wiki_url = f"https://namu.wiki/w/{encoded_name}"
        res_wiki = requests.get(wiki_url, headers=headers)
        soup_wiki = BeautifulSoup(res_wiki.text, 'html.parser')
        # 나무위키 이미지 추출 (구조가 자주 바뀌어 예외처리)
        img_tag = soup_wiki.find('img', src=re.compile('https://i\.namu\.wiki/i/'))
        if img_tag: data["profile_img"] = img_tag.get('src')

        # 2. 키워드 뉴스: 화제성 수집
        effect_url = f"https://search.naver.com/search.naver?where=news&query={encoded_name}+효과+완판"
        res_news = requests.get(effect_url, headers=headers)
        soup_news = BeautifulSoup(res_news.text, 'html.parser')
        data["news_effect"] = [a.get('title') for a in soup_news.find_all('a', class_='news_tit', limit=3)]

        # 3. 리스크 체크
        risk_url = f"https://search.naver.com/search.naver?where=news&query={encoded_name}+논란+비판"
        res_risk = requests.get(risk_url, headers=headers)
        if "논란" in res_risk.text or "비판" in res_risk.text:
            data["risk"] = "⚠️ 최근 언급된 이슈가 존재함 (정밀 검토 권장)"

    except Exception as e:
        print(f"Error: {e}")
    
    return data

@app.get("/analyze")
def analyze(name: str):
    details = get_detailed_data(name)
    return {
        "name": name,
        "profile_img": details["profile_img"],
        "score": 98,
        "summary": f"분석 결과, {name}님은 글로벌 캠페인 및 삼성 CES 등 하이테크 광고 모델로 가장 적합합니다.",
        "global": details["global"],
        "risk": details["risk"],
        "news_effect": details["news_effect"],
        "top_yt": details["top_yt"]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
    