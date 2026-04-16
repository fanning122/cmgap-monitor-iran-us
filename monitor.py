#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判监测脚本（双账号随机比例 4:6，失败自动切换，增量统计，支持图片，自动清理过期数据，中英文翻译）
- 顺序处理 20 个 X 账号
- 每个账号随机选择使用账号1（40%）或账号2（60%）
- 如果所选账号抓取失败（重试后无结果），立即切换另一个账号重试一次
- 两次均失败则跳过该账号
- 新闻抓取部分使用 Selenium，支持 Dawn 和 ARY News
- 抓取推文中的图片并保存到本地（原始尺寸，不替换URL），在 HTML 中显示
- 每次运行时自动删除超过6小时的旧数据和对应的图片文件
- 将英文内容翻译成中文，在页面中同时显示原文和译文
- 生成 HTML 时显示与上次刷新相比的新增内容
"""

import os
import json
import time
import random
import hashlib
import requests
import shutil
from datetime import datetime, timezone, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# ==================== 配置区 ====================
X_ACCOUNTS = [
    "foreignofficepk", "mishaqdar50", "cmshehbaz", "pakpmo", "IranAmbPak",
    "paktvglobal", "Tasnimnews_Fa", "araghchi", "irimfa", "mb_ghalibaf",
    "AJENews", "whitehouse", "usembislamabad", "CBSNews", "JenniferJJacobs",
    "KellieMeyerNews", "realdonaldtrump", "vp", "geonews_urdu", "CGTNEurope"
]

NEWS_URLS = [
    "https://www.dawn.com/latest-news",
    "https://arynews.tv/tag/islamabad-talks"
]

ITEMS_FILE = "items.json"
HTML_FILE = "index.html"
MAX_TWEETS_PER_ACCOUNT = 3
RETRY_DELAY = 3
BETWEEN_ACCOUNTS_DELAY = (3, 5)
BETWEEN_ARTICLES_DELAY = (1, 2)

IMAGES_DIR = "images"

# ==================== 全局浏览器驱动 ====================
_driver = None

def get_driver():
    global _driver
    if _driver is None:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
        _driver = webdriver.Chrome(options=chrome_options)
    return _driver

def close_driver():
    global _driver
    if _driver:
        _driver.quit()
        _driver = None

# ==================== Cookie 注入 ====================
def inject_cookies(driver, account=1):
    """注入指定账号的 Cookie，account=1 或 2"""
    if account == 1:
        auth_token = os.environ.get("X_AUTH_TOKEN")
        ct0 = os.environ.get("X_CT0")
        twid = os.environ.get("X_TWID")
    else:
        auth_token = os.environ.get("X_AUTH_TOKEN2")
        ct0 = os.environ.get("X_CT02")
        twid = os.environ.get("X_TWID2")
    
    if not (auth_token and ct0 and twid):
        print(f"⚠️ 未配置账号{account}的 Cookie，跳过")
        return False
    
    driver.delete_all_cookies()
    driver.get("https://x.com")
    time.sleep(2)
    driver.add_cookie({"name": "auth_token", "value": auth_token, "domain": ".x.com"})
    driver.add_cookie({"name": "ct0", "value": ct0, "domain": ".x.com"})
    driver.add_cookie({"name": "twid", "value": twid, "domain": ".x.com"})
    print(f"✅ 账号{account} Cookie 注入成功")
    return True

# ==================== 翻译函数 ====================
def translate_text(text, target_lang='zh-CN'):
    """将英文文本翻译成中文，失败时返回原文"""
    if not text or len(text.strip()) == 0:
        return text
    try:
        translator = GoogleTranslator(source='auto', target=target_lang)
        translated = translator.translate(text)
        return translated
    except Exception as e:
        print(f"    ⚠️ 翻译失败: {e}")
        return text

# ==================== 下载图片（保持原始URL，不替换尺寸） ====================
def download_image(img_url, save_dir=IMAGES_DIR):
    """下载图片并返回本地文件名，失败返回 None（不修改URL参数）"""
    if not img_url:
        return None
    try:
        os.makedirs(save_dir, exist_ok=True)
        img_hash = hashlib.md5(img_url.encode()).hexdigest()[:16]
        ext = os.path.splitext(img_url.split('?')[0])[1]
        if not ext or len(ext) > 5:
            ext = '.jpg'
        local_filename = f"{img_hash}{ext}"
        local_path = os.path.join(save_dir, local_filename)
        if os.path.exists(local_path):
            return local_filename
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(img_url, headers=headers, timeout=10)
        if response.status_code == 200:
            with open(local_path, 'wb') as f:
                f.write(response.content)
            print(f"    📸 已下载图片: {local_filename}")
            return local_filename
        else:
            print(f"    ⚠️ 下载图片失败: {img_url} (HTTP {response.status_code})")
            return None
    except Exception as e:
        print(f"    ⚠️ 下载图片异常: {img_url} - {e}")
        return None

# ==================== 抓取推文（保留原始图片URL，增加翻译） ====================
def fetch_tweets_from_account(username, driver, account=1, max_tweets=MAX_TWEETS_PER_ACCOUNT, retry=True):
    """使用指定账号的 Cookie 抓取推文（包括图片、翻译），失败时可重试一次"""
    url = f"https://x.com/{username}"
    for attempt in range(1, 3 if retry else 1):
        try:
            driver.get(url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"]'))
            )
            time.sleep(1)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            articles = soup.find_all('article', attrs={'data-testid': 'tweet'})
            tweets = []
            for art in articles[:max_tweets]:
                try:
                    # 推文文本
                    text_div = art.find('div', {'data-testid': 'tweetText'})
                    text = text_div.get_text(strip=True) if text_div else ""
                    if len(text) > 500:
                        text = text[:497] + "..."
                    
                    # 翻译文本
                    translated_text = translate_text(text)
                    
                    # 提取图片原始 URL
                    images = []
                    img_tags = art.find_all('img')
                    for img in img_tags:
                        src = img.get('src')
                        if not src:
                            continue
                        if 'profile_images' in src or 'avatar' in src.lower() or 'twemoji' in src:
                            continue
                        images.append(src)
                    images = list(dict.fromkeys(images))
                    
                    # 下载图片
                    local_images = []
                    for img_url in images:
                        local_img = download_image(img_url)
                        if local_img:
                            local_images.append(local_img)
                    
                    # 推文链接和 ID
                    time_link = art.find('a', href=True)
                    tweet_url = ""
                    tweet_id = ""
                    if time_link and '/status/' in time_link.get('href', ''):
                        tweet_url = "https://x.com" + time_link['href']
                        tweet_id = tweet_url.split('/')[-1]
                    else:
                        tweet_id = str(hash(text))
                    
                    time_tag = art.find('time')
                    pub_time = time_tag['datetime'] if time_tag and time_tag.get('datetime') else datetime.now(timezone.utc).isoformat()
                    
                    tweets.append({
                        "id": f"tweet_{username}_{tweet_id}",
                        "type": "tweet",
                        "username": username,
                        "text": text,
                        "translated_text": translated_text,
                        "images": local_images,
                        "url": tweet_url if tweet_url else f"https://x.com/{username}/status/{tweet_id}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "original_time": pub_time
                    })
                except Exception as e:
                    print(f"    解析单条推文出错: {e}")
                    continue
            if tweets:
                print(f"  从 @{username} (账号{account}) 抓取到 {len(tweets)} 条推文")
                return tweets
            else:
                print(f"  从 @{username} (账号{account}) 未找到推文（尝试 {attempt}/2）")
                if attempt == 1 and retry:
                    time.sleep(RETRY_DELAY)
                    continue
                return []
        except Exception as e:
            print(f"  抓取 @{username} (账号{account}) 失败 (尝试 {attempt}/2): {e}")
            if attempt == 1 and retry:
                time.sleep(RETRY_DELAY)
                continue
            return []
    return []

# ==================== 新闻抓取函数（增加翻译） ====================
def extract_article_links(listpage_url):
    driver = get_driver()
    try:
        print(f"  正在访问列表页: {listpage_url}")
        driver.get(listpage_url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        soup = BeautifulSoup(driver.page_source, "html.parser")
        domain = listpage_url.split("/")[2]
        links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not href or len(href) < 5:
                continue
            if href.startswith('/'):
                full_url = listpage_url.rstrip('/') + href
            elif href.startswith('http'):
                full_url = href
            else:
                continue
            if domain not in full_url:
                continue
            if domain == "arynews.tv":
                path = full_url.replace(f"https://{domain}", "")
                if any(x in path for x in ['/category/', '/tag/', '/author/', '/page/', '/video', '/live']):
                    continue
                if len(path) < 15:
                    continue
                if path in ["", "/"]:
                    continue
                links.add(full_url)
            else:
                if ('/news/' in href or '/story/' in href or '/article/' in href 
                    or '/2026/' in href or href.startswith('/politics/') 
                    or href.startswith('/business/') or href.startswith('/world/')):
                    if any(x in full_url for x in ['/video', '/live', '/gallery', '/tag/', '/category/', '/author/']):
                        continue
                    links.add(full_url)
        result = list(links)[:10]
        print(f"  从 {listpage_url} 提取到 {len(result)} 个文章链接")
        return result
    except Exception as e:
        print(f"  ❌ 提取链接失败 {listpage_url}: {e}")
        return []

def fetch_article_detail(article_url):
    driver = get_driver()
    try:
        driver.get(article_url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        page_title = driver.title.lower()
        if "opt out" in page_title or "privacy" in page_title:
            print(f"  ⚠️ 页面可能被屏蔽，跳过: {article_url[:80]}")
            return None
        soup = BeautifulSoup(driver.page_source, "html.parser")
        title = None
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        if not title:
            t = soup.find("title")
            if t:
                candidate = t.get_text(strip=True)
                if not any(x in candidate.lower() for x in ["opt out", "privacy", "share"]):
                    title = candidate
        if not title:
            title = "无标题"
        
        # 翻译标题
        translated_title = translate_text(title)
        
        pub_time = None
        meta_time = soup.find("meta", {"property": "article:published_time"})
        if meta_time and meta_time.get("content"):
            pub_time = meta_time["content"]
        else:
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                pub_time = time_tag["datetime"]
            elif time_tag:
                pub_time = time_tag.get_text(strip=True)
            else:
                pub_time = datetime.now(timezone.utc).isoformat()
        url_hash = hashlib.md5(article_url.encode()).hexdigest()[:12]
        return {
            "id": f"article_{url_hash}",
            "type": "article",
            "source": article_url.split("/")[2],
            "title": title,
            "translated_title": translated_title,
            "url": article_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_time": pub_time
        }
    except Exception as e:
        print(f"  ❌ 抓取详情失败 {article_url}: {e}")
        return None

# ==================== 数据存储与过滤 ====================
def load_items():
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_items(items):
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

def filter_recent_items(items, hours=6):
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=hours)
    recent = []
    for item in items:
        ts_str = item.get("original_time") or item.get("timestamp")
        if not ts_str:
            continue
        if ts_str.endswith("Z"):
            ts_str = ts_str.replace("Z", "+00:00")
        try:
            item_time = datetime.fromisoformat(ts_str)
        except:
            continue
        if item_time >= cutoff:
            recent.append(item)
    return recent

# ==================== 清理过期数据 ====================
def clean_old_data(hours=6):
    """删除 items.json 中超过 hours 小时的数据，并删除不再被引用的图片文件"""
    if not os.path.exists(ITEMS_FILE):
        return
    with open(ITEMS_FILE, "r", encoding="utf-8") as f:
        all_items = json.load(f)
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=hours)
    
    kept_items = []
    used_images = set()
    for item in all_items:
        ts_str = item.get("original_time") or item.get("timestamp")
        if not ts_str:
            continue
        if ts_str.endswith("Z"):
            ts_str = ts_str.replace("Z", "+00:00")
        try:
            item_time = datetime.fromisoformat(ts_str)
        except:
            continue
        if item_time >= cutoff:
            kept_items.append(item)
            if item.get("type") == "tweet" and item.get("images"):
                for img in item["images"]:
                    used_images.add(img)
    
    if len(kept_items) != len(all_items):
        save_items(kept_items)
        print(f"🧹 已删除 {len(all_items) - len(kept_items)} 条超过 {hours} 小时的旧数据")
    
    if os.path.exists(IMAGES_DIR):
        all_files = os.listdir(IMAGES_DIR)
        deleted_count = 0
        for fname in all_files:
            if fname not in used_images:
                try:
                    os.remove(os.path.join(IMAGES_DIR, fname))
                    deleted_count += 1
                except:
                    pass
        if deleted_count > 0:
            print(f"🧹 已删除 {deleted_count} 个不再使用的图片文件")

# ==================== 增量统计 ====================
def save_last_stats(tweet_count, article_count, update_time_str):
    stats = {
        "tweet_count": tweet_count,
        "article_count": article_count,
        "update_time": update_time_str
    }
    with open("last_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f)

def load_last_stats():
    if os.path.exists("last_stats.json"):
        with open("last_stats.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return None

# ==================== 生成 HTML（中英文显示） ====================
def generate_html(recent_items):
    tweets = [i for i in recent_items if i["type"] == "tweet"]
    articles = [i for i in recent_items if i["type"] == "article"]
    tweets.sort(key=lambda x: x.get("original_time", x.get("timestamp", "")), reverse=True)
    articles.sort(key=lambda x: x.get("original_time", x.get("timestamp", "")), reverse=True)
    
    last_stats = load_last_stats()
    last_tweet_count = last_stats.get("tweet_count", 0) if last_stats else 0
    last_article_count = last_stats.get("article_count", 0) if last_stats else 0
    last_update = last_stats.get("update_time", "从未刷新") if last_stats else "从未刷新"
    
    new_tweets = len(tweets) - last_tweet_count
    new_articles = len(articles) - last_article_count
    if new_tweets < 0:
        new_tweets = 0
    if new_articles < 0:
        new_articles = 0
    
    if new_tweets == 0 and new_articles == 0:
        change_msg = "📭 自上次刷新以来，无新内容。"
    else:
        changes = []
        if new_tweets > 0:
            changes.append(f"{new_tweets} 条新推文")
        if new_articles > 0:
            changes.append(f"{new_articles} 篇新文章")
        change_msg = f"✨ 相比上次（{last_update}），新增 " + "、".join(changes) + "。"
    
    utc_now = datetime.now(timezone.utc)
    pkt_timezone = timezone(timedelta(hours=5))
    pkt_now = utc_now.astimezone(pkt_timezone)
    update_time = pkt_now.strftime("%Y-%m-%d %H:%M:%S PKT")
    
    html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="600">
    <title>美伊谈判监测 · 最近6小时</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        h1 {{ color: #2c3e50; border-left: 5px solid #e74c3c; padding-left: 15px; }}
        .tweet {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #1da1f2; }}
        .tweet .username {{ font-weight: bold; color: #1da1f2; }}
        .tweet .time {{ font-size: 0.8em; color: #7f8c8d; margin-top: 5px; }}
        .tweet .text {{ margin: 10px 0; }}
        .tweet .translated {{ margin: 5px 0; color: #2c3e50; background: #f0f7ff; padding: 5px; border-radius: 5px; }}
        .tweet .images {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
        .tweet .images img {{ max-width: 200px; max-height: 200px; border-radius: 8px; border: 1px solid #ddd; }}
        .article {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #27ae60; }}
        .article .source {{ font-weight: bold; color: #27ae60; }}
        .article .title {{ margin: 8px 0; }}
        .article .translated-title {{ margin: 5px 0; color: #2c3e50; background: #f0f7ff; padding: 5px; border-radius: 5px; }}
        .article .time {{ font-size: 0.8em; color: #7f8c8d; margin-top: 5px; }}
        .footer {{ text-align: center; margin-top: 30px; font-size: 0.8em; color: #7f8c8d; }}
        hr {{ margin: 20px 0; }}
        .status {{ background: #e8f4f8; padding: 10px; border-radius: 8px; margin-bottom: 20px; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>📡 美伊谈判实时监测</h1>
    <div class="status">
        🕒 当前页面数据更新时间：{update_time}<br>
        📊 显示最近6小时内数据 | 页面每10分钟自动刷新<br>
        {change_msg}
    </div>
    <h2>🐦 X 推文 ({tweet_count})</h2>
    {tweets_html}
    <h2>📰 新闻文章 ({article_count})</h2>
    {articles_html}
    <div class="footer">
        <hr>
        <p>数据来源：X平台 + Dawn / ARY News | 自动抓取部署于 GitHub Actions | 淘汰超过6小时的内容 | 英文自动翻译为中文</p>
    </div>
</body>
</html>"""
    
    tweets_html = ""
    for t in tweets:
        images_html = ""
        if t.get("images"):
            for img in t["images"]:
                images_html += f'<img src="images/{img}" alt="推文图片">'
            images_html = f'<div class="images">{images_html}</div>'
        tweets_html += f'''
        <div class="tweet">
            <div class="username">@{t.get("username", "")}</div>
            <div class="text">📝 原文: {t.get("text", "")}</div>
            <div class="translated">🇨🇳 译文: {t.get("translated_text", t.get("text", ""))}</div>
            {images_html}
            <div class="time">🕒 {t.get("original_time", t.get("timestamp", ""))}</div>
            <div><a href="{t.get("url", "#")}" target="_blank">查看原文</a></div>
        </div>
        '''
    if not tweets:
        tweets_html = "<p>暂无最近6小时的新推文。</p>"
    
    articles_html = ""
    for a in articles:
        articles_html += f'''
        <div class="article">
            <div class="source">{a.get("source", "")}</div>
            <div class="title">📝 原文标题: <a href="{a.get("url", "#")}" target="_blank">{a.get("title", "")}</a></div>
            <div class="translated-title">🇨🇳 译文标题: {a.get("translated_title", a.get("title", ""))}</div>
            <div class="time">🕒 {a.get("original_time", a.get("timestamp", ""))}</div>
        </div>
        '''
    if not articles:
        articles_html = "<p>暂无最近6小时的新文章。</p>"
    
    html = html_template.format(
        update_time=update_time,
        tweet_count=len(tweets),
        article_count=len(articles),
        change_msg=change_msg,
        tweets_html=tweets_html,
        articles_html=articles_html
    )
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    
    save_last_stats(len(tweets), len(articles), update_time)

# ==================== 主函数 ====================
def main():
    print(f"{datetime.now()} 开始抓取（支持图片、自动清理、中英文翻译）...")
    
    # 清理超过6小时的旧数据和图片
    clean_old_data(hours=6)
    
    start = time.time()
    driver = get_driver()
    os.makedirs(IMAGES_DIR, exist_ok=True)
    
    account1_available = bool(os.environ.get("X_AUTH_TOKEN") and os.environ.get("X_CT0") and os.environ.get("X_TWID"))
    account2_available = bool(os.environ.get("X_AUTH_TOKEN2") and os.environ.get("X_CT02") and os.environ.get("X_TWID2"))
    if not (account1_available or account2_available):
        print("❌ 未配置任何 X Cookie，跳过 X 推文抓取")
        x_enabled = False
    else:
        x_enabled = True
        if not account1_available:
            print("⚠️ 账号1 Cookie 未配置，将只使用账号2")
        if not account2_available:
            print("⚠️ 账号2 Cookie 未配置，将只使用账号1")
    
    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    new_items = []
    
    if x_enabled:
        print("\n--- 抓取 X 平台推文（随机分配 Cookie，比例 4:6） ---")
        for idx, username in enumerate(X_ACCOUNTS, 1):
            if account1_available and account2_available:
                chosen_account = 1 if random.random() < 0.4 else 2
            elif account1_available:
                chosen_account = 1
            else:
                chosen_account = 2
            
            print(f"[{idx}/{len(X_ACCOUNTS)}] 抓取 @{username} (随机选用账号{chosen_account}) ...")
            if not inject_cookies(driver, account=chosen_account):
                print(f"  ❌ 无法注入账号{chosen_account}的 Cookie，跳过 @{username}")
                continue
            
            tweets = fetch_tweets_from_account(username, driver, account=chosen_account, retry=True)
            if not tweets and ((chosen_account == 1 and account2_available) or (chosen_account == 2 and account1_available)):
                other_account = 2 if chosen_account == 1 else 1
                print(f"  ⚠️ 账号{chosen_account} 抓取失败，尝试切换至账号{other_account}...")
                if inject_cookies(driver, account=other_account):
                    tweets = fetch_tweets_from_account(username, driver, account=other_account, retry=True)
                    if tweets:
                        print(f"  ✅ 切换账号{other_account}后成功抓取")
                else:
                    print(f"  ❌ 无法注入账号{other_account}的 Cookie，无法切换")
            
            for tw in tweets:
                if tw["id"] not in existing_ids:
                    new_items.append(tw)
                    existing_ids.add(tw["id"])
            
            if idx < len(X_ACCOUNTS):
                delay = random.uniform(*BETWEEN_ACCOUNTS_DELAY)
                time.sleep(delay)
    
    print("\n--- 抓取新闻文章 ---")
    for listpage in NEWS_URLS:
        print(f"处理列表页: {listpage}")
        article_links = extract_article_links(listpage)
        if not article_links:
            print(f"  ⚠️ 未提取到链接，跳过该列表页")
            continue
        for idx, article_url in enumerate(article_links, 1):
            print(f"  [{idx}/{len(article_links)}] 抓取: {article_url[:80]}...")
            article = fetch_article_detail(article_url)
            if article and article["id"] not in existing_ids:
                new_items.append(article)
                existing_ids.add(article["id"])
                print(f"    ✅ 新增: {article['title'][:50]}")
            else:
                print(f"    ⏭️ 已存在或失败")
            time.sleep(random.uniform(*BETWEEN_ARTICLES_DELAY))
    
    if new_items:
        all_items.extend(new_items)
        save_items(all_items)
        print(f"\n✅ 总计新增 {len(new_items)} 条内容")
    else:
        print("\n📭 无新增内容")
    
    recent = filter_recent_items(all_items, hours=6)
    generate_html(recent)
    elapsed = time.time() - start
    print(f"\n✅ 已生成 {HTML_FILE}，包含 {len(recent)} 条近期内容")
    print(f"总耗时: {elapsed:.2f} 秒")
    close_driver()

if __name__ == "__main__":
    main()
