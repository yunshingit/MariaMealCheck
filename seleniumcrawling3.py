import time
import re
import requests
import pandas as pd
import numpy as np

from io import StringIO
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import UnexpectedAlertPresentException, NoAlertPresentException

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

print("DEBUG: RUNNING seleniumcrawling3.py - HEADLESS MODE ENABLED")

# 실제 슬랙 웹훅 URL로 교체
WEBHOOK_URL = "https://hooks.slack.com/services/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"

try:
    USER_ID = "dyshin"
    USER_PW = "workMR**1201"

    # 1) 인트라넷 로그인
    driver.get("https://mail.mariababy.com/")
    time.sleep(1)
    driver.find_element(By.ID, "txtUserid").send_keys(USER_ID)
    driver.find_element(By.ID, "txtPassword").send_keys(USER_PW)
    driver.find_element(By.ID, "imgLogin").click()
    print("Login submitted")
    time.sleep(2)

    # 이미 로그인된 세션 경고창 처리
    try:
        alert = driver.switch_to.alert
        alert_text = alert.text
        print(f"Alert detected: {alert_text}")
        if "Already logged in another place" in alert_text:
            print("Closing existing session and continuing login...")
            alert.accept()
            time.sleep(2)
    except NoAlertPresentException:
        print("No existing login alert detected, proceeding normally.")

    # 로그인 실패 체크
    if "ID 와 비밀번호를 정확히 넣어 주십시오." in driver.page_source:
        print("Login failed.")
        driver.quit()
        exit()
    print("Login successful")

    # 2) 식단표 게시판 이동 -> 최신 글
    driver.get("https://mail.mariababy.com/bbs/bbs_list.aspx?bbs_num=41")
    time.sleep(1)
    latest_post = driver.find_element(By.XPATH, '//a[contains(@href, "read_bbs.aspx")]/span')
    post_title = latest_post.text.strip()
    post_link = latest_post.find_element(By.XPATH, "./..").get_attribute("href")
    print(f"Latest Post: {post_title} | {post_link}")

    driver.get(post_link)
    time.sleep(2)

    # 3) 본문 내 식단표 테이블 추출
    table_elem = driver.find_element(By.CLASS_NAME, "__se_tbl_ext")
    table_html = table_elem.get_attribute("outerHTML")

    # 4) pandas로 파싱
    df_list = pd.read_html(StringIO(table_html), flavor="lxml")
    if not df_list:
        raise ValueError("식단표 테이블을 찾지 못했습니다.")
    df = df_list[0]
    print("DataFrame shape:", df.shape)

    # 5) 하단 안내문(푸터) 제거
    if df.shape[0] > 9:
        df = df.iloc[:-3, :]
    print("After cutting footer:", df.shape)

    # 6) 첫 행 -> 날짜 (두 번째 열부터)
    header_row = df.iloc[0].tolist()
    dates_raw = header_row[1:]
    dates = []
    for val in dates_raw:
        if pd.isna(val):
            dates.append("")
        else:
            dates.append(str(val).strip())

    # 날짜-메뉴 dict
    menu_dict = {}
    for d in dates:
        if d:
            menu_dict[d] = []

    # 7) 나머지 행 -> 메뉴
    for row_idx in range(1, df.shape[0]):
        row_data = df.iloc[row_idx].tolist()
        for col_idx, date_str in enumerate(dates, start=1):
            if not date_str:
                continue

            # 범위 체크
            if col_idx < len(row_data):
                cell_val = row_data[col_idx]
            else:
                cell_val = np.nan

            if pd.isna(cell_val):
                continue

            # 값 파싱
            cell_text = str(cell_val).strip()
            # 괄호 제거
            cell_text = re.sub(r"\(.*?\)", "", cell_text)
            # 줄바꿈 분리
            lines = [ln.strip() for ln in cell_text.split("\n") if ln.strip()]

            for ln in lines:
                menu_dict[date_str].append(ln)

    # 8) 연속된 동일 항목 제거
    for d in menu_dict:
        original_list = menu_dict[d]
        deduplicated_list = []
        for m in original_list:
            if not deduplicated_list or deduplicated_list[-1] != m:
                deduplicated_list.append(m)
        menu_dict[d] = deduplicated_list

    # 9) Slack Block Kit 구성

    # 날짜 필터링 (예: 월~금만) -> 원하는 경우에만 적용
    # 여기서는 일단 모든 날짜 사용
    filtered_dates = [d for d in dates if d in menu_dict]

    # 블록 배열
    blocks = []

    # (선택) 헤더 블록 (식단표 제목)
    title_block = {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": post_title,  # 예: "3/17~3/23 식단표"
            "emoji": True
        }
    }
    blocks.append(title_block)

    # 날짜 + 메뉴 표시 + divider
    for d in filtered_dates:
        # 왼쪽 필드 (날짜)
        left_field = {
            "type": "mrkdwn",
            "text": f"*{d}*"
        }
        # 오른쪽 필드 (메뉴들 줄바꿈)
        menu_text = "\n".join(menu_dict[d]) if d in menu_dict else "(메뉴 없음)"
        right_field = {
            "type": "mrkdwn",
            "text": menu_text
        }

        # 날짜 한 건 = fields 2개
        day_block = {
            "type": "section",
            "fields": [left_field, right_field]
        }
        blocks.append(day_block)

        # 날짜 사이에 divider 추가
        blocks.append({"type": "divider"})

    # 마지막 divider 제거(원한다면)
    if blocks and blocks[-1]["type"] == "divider":
        blocks.pop()

    payload = {
        "blocks": blocks
    }

    print("\n===== 최종 Block Kit 메시지 =====\n", payload)

    # Slack 전송
    resp = requests.post(WEBHOOK_URL, json=payload)
    if resp.status_code == 200:
        print("Menu data sent to Slack successfully.")
    else:
        print(f"Failed to send Slack message: {resp.status_code}")

finally:
    driver.quit()
