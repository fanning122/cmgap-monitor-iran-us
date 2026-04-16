#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判监测脚本（最终稳定版）
- 使用 Selenium 模拟浏览器 + 你的 Cookie 抓取 X 账号推文
- 每个账号先抓一次，失败则重试一次，成功则跳过第二次
- 抓取 Dawn 和 ARY News 列表页的最新文章
- 保存到 items.json，基于发布时间过滤最近6小时
- 生成 index.html 并部署到 GitHub Pages
- 总运行时间目标 ≤ 8 分钟
"""

import os
import json
import time
import random
import hashlib
from datetime import datetime, timezone, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

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
MAX_TWEETS_PER_ACCOUNT = 3       # 每个账号最多抓取3条推文
RETRY_DELAY = 3                   # 重试前等待3秒
BETWEEN_ACCOUNTS_DELAY = (3, 5)  # 账号之间的随机延迟（秒）
BETWEEN_ARTICLES_DELAY = (1, 2)  # 文章之间的随机延迟（秒）

# ==================== 全局浏览器驱动（复用） ====================
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

# ==================== 注入 X Cookie ====================
def inject_x_cookies(driver):
    """从环境变量读取 Cookie 并注入到浏览器"""
    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")
    twid = os.environ.get("X_TWID")
    if not (auth_token and ct0 and twid):
        print("⚠️ 未配置 X Cookie (X_AUTH_TOKEN, X_CT0, X_TWID)，跳过 X 推文抓取")
        return False
    # 先访问 x.com 建立域名
    driver.get("https://x.com")
    time.sleep(2)
    driver.add_cookie({"name": "auth_token", "value": auth_token, "domain": ".x.com"})
    driver.add_cookie({"name": "ct0", "value": ct0, "domain": ".x.com"})
    driver.add_cookie({"name": "twid", "value": twid, "domain": ".x.com"})
    print("✅ X Cookie 注入成功")
    return True

# ==================== 从 X 页面抓取推文（带重试） ====================
def fetch_tweets_from_account(username, driver, max_tweets=MAX_TWEETS_PER_ACCOUNT, retry=True):
    """抓取一个账号的推文，失败时可重试一次"""
    url = f"https://x.com/{username}"
    for attempt in range(1, 3 if retry else 1):
        try:
            driver.get(url)
            # 等待推文出现，最长15秒
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"]'))
            )
            # 额外等待1秒让JS渲染
            time.sleep(1)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            articles = soup.find_all('article', attrs={'data-testid': 'tweet'})
            tweets = []
            for art in articles[:max_tweets]:
                try:
                    text_div = art.find('div', {'data-testid': 'tweetText'})
                    text = text_div.get_text(strip=True) if text_div else ""
                    if len(text) > 500:
                        text = text[:497] + "..."
                    # 推文链接
                    time_link = art.find('a', href=True)
                    tweet_url = ""
                    tweet_id = ""
                    if time_link and '/status/' in time_link.get('href', ''):
                        tweet_url = "https://x.com" + time_link['href']
                        tweet_id = tweet_url.split('/')[-1]
                    else:
                        tweet_id = str(hash(text))
                    # 发布时间
                    time_tag = art.find('time')
                    pub_time = time_tag['datetime'] if time_tag and time_tag.get('datetime') else datetime.now(timezone.utc).isoformat()
                    tweets.append({
                        "id": f"tweet_{username}_{tweet_id}",
                        "type": "tweet",
                        "username": username,
                        "text": text,
                        "url": tweet_url if tweet_url else f"https://x.com/{username}/status/{tweet_id}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "original_time": pub_time
                    })
                except Exception as e:
                    print(f"    解析单条推文出错: {e}")
                    continue
            if tweets:
                print(f"  从 @{username} 抓取到 {len(tweets)} 条推文")
                return tweets
            else:
                print(f"  从 @{username} 未找到推文（尝试 {attempt}/2）")
                if attempt == 1 and retry:
                    time.sleep(RETRY_DELAY)
                    continue
                return []
        except Exception as e:
            print(f"  抓取 @{username} 失败 (尝试 {attempt}/2): {e}")
            if attempt == 1 and retry:
                time.sleep(RETRY_DELAY)
                continue
            return []
    return []

# ==================== 新闻抓取（复用同一个 driver） ====================
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
            # 转换为绝对URL
            if href.startswith('/'):
                full_url = listpage_url.rstrip('/') + href
            elif href.startswith('http'):
                full_url = href
            else:
                continue
            # 只保留同域名链接
            if domain not in full_url:
                continue
            
            # 根据域名采用不同规则
            if domain == "arynews.tv":
                # ARY News 文章路径特征：不含 /category/ /tag/ /author/ /page/ 等，且路径长度 > 15
                path = full_url.replace(f"https://{domain}", "")
                if any(x in path for x in ['/category/', '/tag/', '/author/', '/page/', '/video', '/live']):
                    continue
                if len(path) < 15:
                    continue
                # 排除首页本身
                if path in ["", "/"]:
                    continue
                links.add(full_url)
            else:  # dawn.com 及其他
                if ('/news/' in href or '/story/' in href or '/article/' in href 
                    or '/2026/' in href or href.startswith('/politics/') 
                    or href.startswith('/business/') or href.startswith('/world/')):
                    # 排除非文章页
                    if any(x in full_url for x in ['/video', '/live', '/gallery', '/tag/', '/category/', '/author/']):
                        continue
                    links.add(full_url)
        
        result = list(links)[:10]  # 限制最多10个
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
        # 检查页面标题是否包含隐私提示（Dawn 屏蔽标识）
        page_title = driver.title.lower()
        if "opt out" in page_title or "privacy" in page_title or "share" in page_title:
            print(f"  ⚠️ 页面可能被屏蔽（标题含隐私提示），跳过: {article_url[:80]}")
            return None

        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        # 尝试多种方式获取真实标题
        title = None
        # 1) og:title
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
        # 2) h1
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        # 3) title 标签（但要排除隐私提示）
        if not title:
            t = soup.find("title")
            if t:
                candidate = t.get_text(strip=True)
                if not any(x in candidate.lower() for x in ["opt out", "privacy", "share"]):
                    title = candidate
        if not title:
            title = "无标题"
        
        # 发布时间（保持不变）
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

# ==================== 生成 HTML ====================
def generate_html(recent_items):
    tweets = [i for i in recent_items if i["type"] == "tweet"]
    articles = [i for i in recent_items if i["type"] == "article"]
    tweets.sort(key=lambda x: x.get("original_time", x.get("timestamp", "")), reverse=True)
    articles.sort(key=lambda x: x.get("original_time", x.get("timestamp", "")), reverse=True)

    html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>美伊谈判监测 · 最近6小时</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        h1 {{ color: #2c3e50; border-left: 5px solid #e74c3c; padding-left: 15px; }}
        .tweet {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #1da1f2; }}
        .tweet .username {{ font-weight: bold; color: #1da1f2; }}
        .tweet .time {{ font-size: 0.8em; color: #7f8c8d; margin-top: 5px; }}
        .article {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #27ae60; }}
        .article .source {{ font-weight: bold; color: #27ae60; }}
        .article .time {{ font-size: 0.8em; color: #7f8c8d; margin-top: 5px; }}
        .footer {{ text-align: center; margin-top: 30px; font-size: 0.8em; color: #7f8c8d; }}
        hr {{ margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>📡 美伊谈判实时监测</h1>
    <p>🕒 更新时间：{update_time} | 显示最近6小时内数据 | 每10分钟自动刷新</p>
    <h2>🐦 X 推文 ({tweet_count})</h2>
    {tweets_html}
    <h2>📰 新闻文章 ({article_count})</h2>
    {articles_html}
    <div class="footer">
        <hr>
        <p>数据来源：X平台 + Dawn / ARY News | 自动抓取部署于 GitHub Actions | 淘汰超过6小时的内容</p>
    </div>
</body>
</html>"""

    tweets_html = ""
    for t in tweets:
        tweets_html += f'''
        <div class="tweet">
            <div class="username">@{t.get("username", "")}</div>
            <div>{t.get("text", "")}</div>
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
            <div><a href="{a.get("url", "#")}" target="_blank">{a.get("title", "")}</a></div>
            <div class="time">🕒 {a.get("original_time", a.get("timestamp", ""))}</div>
        </div>
        '''
    if not articles:
        articles_html = "<p>暂无最近6小时的新文章。</p>"

    utc_now = datetime.now(timezone.utc)
    pkt_timezone = timezone(timedelta(hours=5))
    pkt_now = utc_now.astimezone(pkt_timezone)
    update_time = pkt_now.strftime("%Y-%m-%d %H:%M:%S PKT")

    html = html_template.format(
        update_time=update_time,
        tweet_count=len(tweets),
        article_count=len(articles),
        tweets_html=tweets_html,
        articles_html=articles_html
    )
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

# ==================== 主函数 ====================
def main():
    print(f"{datetime.now()} 开始抓取（最终稳定版）...")
    start = time.time()
    driver = get_driver()
    x_enabled = inject_x_cookies(driver)

    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    new_items = []

    # 1. 抓取 X 推文（带重试，失败跳过）
    if x_enabled:
        print("\n--- 抓取 X 平台推文 ---")
        for idx, username in enumerate(X_ACCOUNTS, 1):
            print(f"[{idx}/{len(X_ACCOUNTS)}] 抓取 @{username} ...")
            tweets = fetch_tweets_from_account(username, driver, retry=True)
            for tw in tweets:
                if tw["id"] not in existing_ids:
                    new_items.append(tw)
                    existing_ids.add(tw["id"])
            # 账号间延迟
            if idx < len(X_ACCOUNTS):
                delay = random.uniform(*BETWEEN_ACCOUNTS_DELAY)
                time.sleep(delay)
    else:
        print("\n⚠️ 跳过 X 推文抓取（未配置 Cookie）")

    # 2. 抓取新闻文章（必须执行，失败只打印错误）
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

    # 3. 保存新内容
    if new_items:
        all_items.extend(new_items)
        save_items(all_items)
        print(f"\n✅ 总计新增 {len(new_items)} 条内容")
    else:
        print("\n📭 无新增内容")

    # 4. 过滤最近6小时并生成 HTML
    recent = filter_recent_items(all_items, hours=6)
    generate_html(recent)
    elapsed = time.time() - start
    print(f"\n✅ 已生成 {HTML_FILE}，包含 {len(recent)} 条近期内容")
    print(f"总耗时: {elapsed:.2f} 秒")

    close_driver()

if __name__ == "__main__":
    main()
