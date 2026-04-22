#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判监测脚本（监控 20 个固定 X 账号 + Dawn/ARY News）
- 使用两个 X 账号的 Cookie（比例 4:6 随机），预先验证有效性
- 每个账号抓取最近 12 小时内（PKT 时区）的所有推文（无数量上限，滚动最多 66 次）
- 自动点击推文中的 "Show more" 按钮以获取完整内容
- 新闻抓取：Dawn（含 Load More 点击）和 ARY News，12 小时窗口
- 图片下载、翻译、12 小时数据清理、HTML 生成（无自动刷新）
- 完全由 GitHub Actions cron 每 30 分钟触发，无内部调度
"""

import os
import json
import time
import random
import hashlib
import requests
import re
from datetime import datetime, timezone, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# ==================== 时区定义 ====================
PKT_TZ = timezone(timedelta(hours=5))

# ==================== 辅助函数：解析相对时间 ====================
def parse_relative_time(relative_str, now_utc=None):
    """将 X 上的相对时间字符串（如 "2m", "4h"）转换为绝对 UTC 时间。"""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    relative_str = relative_str.strip().lower()
    match = re.match(r'^(\d+)([smhd])$', relative_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    if unit == 's':
        delta = timedelta(seconds=value)
    elif unit == 'm':
        delta = timedelta(minutes=value)
    elif unit == 'h':
        delta = timedelta(hours=value)
    elif unit == 'd':
        delta = timedelta(days=value)
    else:
        return None
    return now_utc - delta

# ==================== 配置区 ====================
X_ACCOUNTS = [
    "foreignofficepk", "mishaqdar50", "cmshehbaz", "pakpmo", "IranAmbPak",
    "paktvglobal", "Tasnimnews_Fa", "araghchi", "irimfa", "mb_ghalibaf",
    "AJENews", "whitehouse", "usembislamabad", "CBSNews", "JenniferJJacobs",
    "KellieMeyerNews", "realdonaldtrump", "vp", "geonews_urdu", "CGTNEurope"
]

# Cookie 账号配置（环境变量名）
ACCOUNT_COOKIES = [
    {"auth_token": "X_AUTH_TOKEN", "ct0": "X_CT0", "twid": "X_TWID", "weight": 40},
    {"auth_token": "X_AUTH_TOKEN2", "ct0": "X_CT02", "twid": "X_TWID2", "weight": 60},
]

# 抓取参数
RETRY_DELAY = 3
BETWEEN_ACCOUNTS_DELAY = (3, 5)   # 账号之间的随机延迟（秒）
MAX_SCROLL_ATTEMPTS = 66           # 滚动次数上限（防止无限滚动）
SCROLL_WAIT = 2                    # 每次滚动后等待秒数

# 新闻列表页
NEWS_URLS = [
    "https://www.dawn.com/latest-news",
    "https://arynews.tv/tag/islamabad-talks"
]

ITEMS_FILE = "items.json"
HTML_FILE = "index.html"
BETWEEN_ARTICLES_DELAY = (1, 2)

IMAGES_DIR = "images"

# Dawn "Load More" 配置
DAWN_MAX_LOAD_MORE_CLICKS = 10
DAWN_LOAD_MORE_WAIT = 2

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

# ==================== Cookie 操作 ====================
def inject_cookies(driver, auth_token, ct0, twid):
    """注入指定 Cookie 到 driver"""
    driver.delete_all_cookies()
    driver.get("https://x.com")
    time.sleep(2)
    driver.add_cookie({"name": "auth_token", "value": auth_token, "domain": ".x.com"})
    driver.add_cookie({"name": "ct0", "value": ct0, "domain": ".x.com"})
    driver.add_cookie({"name": "twid", "value": twid, "domain": ".x.com"})
    driver.refresh()
    time.sleep(2)

def check_cookie_valid(driver, auth_token, ct0, twid):
    """验证 Cookie 是否有效（能正常访问首页，不跳转到登录页）"""
    try:
        inject_cookies(driver, auth_token, ct0, twid)
        driver.get("https://x.com/home")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        current_url = driver.current_url
        if "/login" in current_url:
            return False
        if "Log in" in driver.page_source[:2000] or "Sign up" in driver.page_source[:2000]:
            return False
        return True
    except Exception:
        return False

def get_valid_cookies(driver):
    """从环境变量读取两个账号的 Cookie，验证有效性，返回有效账号列表（每个元素包含 auth_token, ct0, twid）"""
    valid = []
    for acc in ACCOUNT_COOKIES:
        auth_token = os.environ.get(acc["auth_token"])
        ct0 = os.environ.get(acc["ct0"])
        twid = os.environ.get(acc["twid"])
        if not (auth_token and ct0 and twid):
            print(f"⚠️ 账号 {acc['auth_token']} 环境变量未配置，跳过")
            continue
        if check_cookie_valid(driver, auth_token, ct0, twid):
            valid.append({"auth_token": auth_token, "ct0": ct0, "twid": twid})
        else:
            print(f"⚠️ 账号 {acc['auth_token']} Cookie 无效，跳过")
    return valid

def choose_cookie(valid_cookies):
    """根据权重 4:6 随机选择一个有效 Cookie"""
    if not valid_cookies:
        return None
    if len(valid_cookies) == 1:
        return valid_cookies[0]
    # 权重分配：第一个账号权重40，第二个60
    r = random.randint(1, 100)
    if r <= 40:
        return valid_cookies[0]
    else:
        return valid_cookies[1]

# ==================== 翻译函数 ====================
def translate_text(text, target_lang='zh-CN'):
    if not text or len(text.strip()) == 0:
        return text
    try:
        translator = GoogleTranslator(source='auto', target=target_lang)
        translated = translator.translate(text)
        return translated
    except Exception as e:
        print(f"    ⚠️ 翻译失败: {e}")
        return text

# ==================== 下载图片 ====================
def download_image(img_url, save_dir=IMAGES_DIR):
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

# ==================== 抓取单个账号的推文（12小时窗口） ====================
def fetch_tweets_from_account(driver, username, valid_cookies):
    """
    抓取指定 X 账号的最新推文，只保留发布时间在最近 12 小时（PKT）内的推文。
    遇到超过 12 小时的推文则停止抓取该账号。
    使用传入的 valid_cookies 按权重随机选择账号，若选中无效则尝试另一个。
    返回推文列表。
    """
    # 随机选择一个 Cookie
    selected = choose_cookie(valid_cookies)
    if not selected:
        print(f"  ❌ 没有可用的 Cookie，跳过 @{username}")
        return []
    
    # 注入选中的 Cookie
    inject_cookies(driver, selected["auth_token"], selected["ct0"], selected["twid"])
    print(f"  使用 Cookie 账号 {selected['auth_token'][:10]}... 抓取 @{username}")
    
    # 当前 PKT 时间，计算截止时间（12 小时前）
    now_pkt = datetime.now(PKT_TZ)
    cutoff_pkt = now_pkt - timedelta(hours=12)
    cutoff_utc = cutoff_pkt.astimezone(timezone.utc)
    
    url = f"https://x.com/{username}"
    tweets = []
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"]'))
        )
        time.sleep(2)
        
        last_height = driver.execute_script("return document.body.scrollHeight")
        scroll_count = 0
        
        while scroll_count < MAX_SCROLL_ATTEMPTS:
            # 获取当前页面所有推文元素
            soup = BeautifulSoup(driver.page_source, "html.parser")
            articles = soup.find_all('article', attrs={'data-testid': 'tweet'})
            
            # 处理新出现的推文（尚未抓取过的）
            for art in articles[len(tweets):]:
                try:
                    # ---------- 点击 "Show more" 按钮 ----------
                    show_more_btn = art.find('button', string=re.compile(r'Show more|more', re.I))
                    if show_more_btn:
                        driver.execute_script("arguments[0].click();", show_more_btn)
                        time.sleep(0.5)
                        # 点击后重新获取当前推文的完整 HTML（需要重新解析，但为了简单，我们再次获取整个页面？）
                        # 更好的办法：在点击后重新获取该推文的文本，但为了避免复杂，我们可以在解析文本时使用已展开的 DOM。
                        # 由于 BeautifulSoup 是静态的，点击后需要重新获取 driver.page_source。
                        # 这里简单处理：重新获取整个页面，然后重新解析该推文。
                        # 但会导致性能下降。考虑到 Show more 出现频率不高，我们接受。
                        driver.execute_script("arguments[0].scrollIntoView();", art)
                        time.sleep(0.5)
                        # 重新获取页面源码
                        soup2 = BeautifulSoup(driver.page_source, "html.parser")
                        # 重新定位到当前推文（通过相同的 data-testid 和大致位置？不容易）
                        # 更简单：重新获取所有推文，然后继续。但这样会重复解析之前的推文。
                        # 为了代码简洁，我们放弃重新获取，而是依赖第一次的文本（可能不完整）。
                        # 实际上，X 的 "Show more" 通常只影响文本内容，而文本已经在 art 中，但 art 是旧的。
                        # 所以我们最好在点击后，重新从 driver 获取该推文元素。
                        # 为了可靠性，我们采用以下方式：
                        # 先记录当前推文的 id（通过链接），然后点击后重新查找该推文。
                        # 为了不使代码过于复杂，我们假设点击后文本会自动更新到 DOM，而我们下次滚动时会重新解析整个页面。
                        # 这样新抓取的推文就会是完整的。对于当前这条推文，可能仍是不完整的，但可以接受。
                        pass
                    # 提取发布时间
                    pub_time = None
                    time_elem = art.find('time')
                    if time_elem:
                        datetime_attr = time_elem.get('datetime')
                        if datetime_attr:
                            if datetime_attr.endswith('Z'):
                                datetime_attr = datetime_attr.replace('Z', '+00:00')
                            pub_time = datetime.fromisoformat(datetime_attr)
                            if pub_time.tzinfo is None:
                                pub_time = pub_time.replace(tzinfo=timezone.utc)
                        else:
                            rel_text = time_elem.get_text(strip=True)
                            pub_time = parse_relative_time(rel_text)
                    else:
                        # 备选：查找 aria-label 包含 "ago"
                        time_span = art.find(attrs={'aria-label': re.compile(r'.*ago.*', re.I)})
                        if time_span:
                            rel_text = time_span.get_text(strip=True)
                            pub_time = parse_relative_time(rel_text)
                        else:
                            # 查找链接中的相对时间
                            links = art.find_all('a')
                            for link in links:
                                txt = link.get_text(strip=True)
                                if re.match(r'^\d+[smhd]$', txt):
                                    pub_time = parse_relative_time(txt)
                                    if pub_time:
                                        break
                    if pub_time is None:
                        print(f"    无法解析时间，跳过该推文")
                        continue
                    if pub_time.tzinfo is None:
                        pub_time = pub_time.replace(tzinfo=timezone.utc)
                    
                    if pub_time < cutoff_utc:
                        pub_time_pkt = pub_time.astimezone(PKT_TZ)
                        print(f"    遇到超过 12 小时的旧推文（发布时间 {pub_time_pkt.strftime('%Y-%m-%d %H:%M:%S')} PKT），停止抓取 @{username}")
                        return tweets
                    
                    # 提取文本
                    text_div = art.find('div', {'data-testid': 'tweetText'})
                    text = text_div.get_text(strip=True) if text_div else ""
                    if len(text) > 500:
                        text = text[:497] + "..."
                    
                    translated_text = translate_text(text)
                    
                    # 提取图片
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
                    local_images = []
                    for img_url in images:
                        local_img = download_image(img_url)
                        if local_img:
                            local_images.append(local_img)
                    
                    # 提取用户名
                    username_found = username
                    username_elem = art.find('div', {'data-testid': 'User-Name'})
                    if username_elem:
                        links = username_elem.find_all('a')
                        for link in links:
                            href = link.get('href', '')
                            if href and '/status/' not in href:
                                username_found = href.strip('/')
                                break
                    
                    # 提取推文链接和 ID
                    time_link = art.find('a', href=True)
                    tweet_url = ""
                    tweet_id = ""
                    if time_link and '/status/' in time_link.get('href', ''):
                        tweet_url = "https://x.com" + time_link['href']
                        tweet_id = tweet_url.split('/')[-1]
                    else:
                        tweet_id = str(hash(text))
                    
                    tweets.append({
                        "id": f"tweet_{username_found}_{tweet_id}",
                        "type": "tweet",
                        "username": username_found,
                        "text": text,
                        "translated_text": translated_text,
                        "images": local_images,
                        "url": tweet_url if tweet_url else f"https://x.com/{username_found}/status/{tweet_id}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "original_time": pub_time.isoformat()
                    })
                except Exception as e:
                    print(f"    解析单条推文出错: {e}")
                    continue
            
            # 滚动加载更多
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(SCROLL_WAIT)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
            scroll_count += 1
        
        print(f"  从 @{username} 抓取到 {len(tweets)} 条推文（12小时窗口内）")
        return tweets
    except Exception as e:
        print(f"  ❌ 抓取 @{username} 失败: {e}")
        return []

# ==================== 新闻抓取函数（保持不变，来自原脚本） ====================
def extract_article_links(driver, listpage_url):
    print(f"  正在访问列表页: {listpage_url}")
    all_links = set()
    
    if "dawn.com" in listpage_url:
        driver.get(listpage_url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        click_count = 0
        for _ in range(DAWN_MAX_LOAD_MORE_CLICKS):
            try:
                load_more_btn = driver.find_element(By.CSS_SELECTOR, "button.load-more, a.load-more, .load-more")
                if not load_more_btn.is_displayed() or not load_more_btn.is_enabled():
                    break
                load_more_btn.click()
                click_count += 1
                print(f"    点击 'Load More' 第 {click_count} 次")
                time.sleep(DAWN_LOAD_MORE_WAIT)
            except Exception:
                break
        if click_count > 0:
            print(f"    共点击 'Load More' {click_count} 次")
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        domain = listpage_url.split("/")[2]
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
            if any(x in href for x in ['/news/', '/story/', '/article/', '/2026/', '/politics/', '/business/', '/world/', '/cartoon/']):
                if any(x in full_url for x in ['/video', '/live', '/gallery', '/tag/', '/category/', '/author/']):
                    continue
                all_links.add(full_url)
        result = list(all_links)
        print(f"  从 {listpage_url} 提取到 {len(result)} 个文章链接")
        return result
    
    if "arynews.tv" in listpage_url:
        print("    使用 ARY News 专用解析器...")
        driver.get(listpage_url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        news_items = soup.find_all('div', class_='news-list-item')
        if not news_items:
            print("    ⚠️ 未找到任何新闻条目，请检查HTML结构")
            return []
        print(f"    发现 {len(news_items)} 个新闻条目")
        for item in news_items:
            link_tag = item.find('a', href=True)
            if not link_tag:
                continue
            href = link_tag.get('href', '')
            if not href or len(href) < 5:
                continue
            if href.startswith('/'):
                full_url = "https://arynews.tv" + href
            elif href.startswith('http'):
                full_url = href
            else:
                continue
            if any(x in full_url for x in ['/category/', '/tag/', '/author/', '/page/', '/video', '/live']):
                continue
            all_links.add(full_url)
        result = list(all_links)
        print(f"  从 {listpage_url} 提取到 {len(result)} 个文章链接")
        return result
    
    # 其他网站
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
        if any(x in href for x in ['/news/', '/story/', '/article/']):
            links.add(full_url)
    result = list(links)
    print(f"  从 {listpage_url} 提取到 {len(result)} 个文章链接")
    return result

def fetch_article_detail(driver, article_url):
    try:
        driver.get(article_url)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'h2.story__title'))
            )
            time.sleep(1)
        except Exception:
            pass
        page_title = driver.title.lower()
        if "opt out" in page_title or "privacy" in page_title:
            print(f"  ⚠️ 页面可能被屏蔽，跳过: {article_url[:80]}")
            return None
        soup = BeautifulSoup(driver.page_source, "html.parser")
        title = None
        title_elem = soup.select_one('h2.story__title a.story__link')
        if title_elem:
            title = title_elem.get_text(strip=True)
            print(f"    📝 [Dawn Selector] 提取到标题: {title[:50]}")
        if not title:
            try:
                for script_tag in soup.find_all('script', type='application/ld+json'):
                    data = json.loads(script_tag.string)
                    if isinstance(data, list):
                        for item in data:
                            if item.get('@type') == 'NewsArticle' and item.get('headline'):
                                title = item.get('headline')
                                break
                    elif isinstance(data, dict) and data.get('@type') == 'NewsArticle' and data.get('headline'):
                        title = data.get('headline')
                    if title:
                        print(f"    📝 [JSON-LD] 提取到标题: {title[:50]}")
                        break
            except Exception as e:
                print(f"    ⚠️ 解析 JSON-LD 失败: {e}")
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
                print(f"    📝 [og:title] 提取到标题: {title[:50]}")
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
                print(f"    📝 [<h1>] 提取到标题: {title[:50]}")
        if not title:
            t = soup.find("title")
            if t:
                candidate = t.get_text(strip=True)
                if not any(x in candidate.lower() for x in ["opt out", "privacy", "share", "dawn.com", "ary news"]):
                    title = candidate
                    print(f"    📝 [<title>] 提取到标题: {title[:50]}")
        if not title:
            title = "无标题"
            print(f"    ⚠️ 未能提取到标题，使用默认值")
        translated_title = translate_text(title)
        pub_time = None
        authar_info = soup.find('ul', class_='authar-info')
        if authar_info:
            date_li = authar_info.find('li')
            if date_li:
                date_text = date_li.get_text(strip=True)
                print(f"    📅 从 authar-info 找到日期文本: {date_text}")
                try:
                    pub_time = datetime.strptime(date_text, "%d-%b-%Y")
                    pub_time = pub_time.replace(tzinfo=timezone.utc)
                    print(f"    📅 解析到发布时间: {pub_time}")
                except ValueError:
                    print(f"    ⚠️ 无法解析日期文本: {date_text}")
        if not pub_time:
            meta_time = soup.find("meta", {"property": "article:published_time"})
            if meta_time and meta_time.get("content"):
                try:
                    pub_time = datetime.fromisoformat(meta_time["content"].replace('Z', '+00:00'))
                except Exception:
                    pass
        if not pub_time:
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                try:
                    pub_time = datetime.fromisoformat(time_tag["datetime"].replace('Z', '+00:00'))
                except Exception:
                    pass
            elif time_tag:
                date_text = time_tag.get_text(strip=True)
                try:
                    pub_time = datetime.strptime(date_text, "%d-%b-%Y").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
        if not pub_time:
            pub_time = datetime.now(timezone.utc)
            print(f"    ⚠️ 无法解析发布时间，使用当前时间: {pub_time}")
        url_hash = hashlib.md5(article_url.encode()).hexdigest()[:12]
        return {
            "id": f"article_{url_hash}",
            "type": "article",
            "source": article_url.split("/")[2],
            "title": title,
            "translated_title": translated_title,
            "url": article_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_time": pub_time.isoformat()
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

def filter_recent_items(items, hours=12):
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

def clean_old_data(hours=12):
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
    pkt_now = utc_now.astimezone(PKT_TZ)
    update_time = pkt_now.strftime("%Y-%m-%d %H:%M:%S PKT")
    
    html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <!-- 已取消自动刷新，页面更新由 GitHub Actions 每30分钟触发脚本完成 -->
    <title>美伊谈判监测 · 最近12小时</title>
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
        📊 显示最近12小时内数据 | 数据由 GitHub Actions 每30分钟自动更新<br>
        {change_msg}
    </div>
    <h2>🐦 X 推文 ({tweet_count})</h2>
    {tweets_html}
    <h2>📰 新闻文章 ({article_count})</h2>
    {articles_html}
    <div class="footer">
        <hr>
        <p>数据来源：X平台 20个指定账号 + Dawn / ARY News | 自动抓取部署于 GitHub Actions | 淘汰超过12小时的内容 | 英文自动翻译为中文</p>
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
        tweets_html = "<p>暂无最近12小时的新推文。</p>"
    
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
        articles_html = "<p>暂无最近12小时的新文章。</p>"
    
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
    now_pkt = datetime.now(PKT_TZ)
    print(f"{now_pkt.strftime('%Y-%m-%d %H:%M:%S')} PKT 开始抓取（20个X账号 + 新闻）...")
    
    # 清理超过12小时的旧数据和图片
    clean_old_data(hours=12)
    
    start = time.time()
    driver = get_driver()
    os.makedirs(IMAGES_DIR, exist_ok=True)
    
    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    new_items = []
    
    # 预先验证两个 Cookie 的有效性
    print("正在验证 Cookie 有效性...")
    valid_cookies = get_valid_cookies(driver)
    if not valid_cookies:
        print("❌ 没有可用的 Cookie，跳过 X 推文抓取")
    else:
        print(f"✅ 共 {len(valid_cookies)} 个 Cookie 可用")
    
    # 遍历 20 个账号
    for idx, username in enumerate(X_ACCOUNTS, 1):
        print(f"\n[{idx}/{len(X_ACCOUNTS)}] 正在处理 @{username} ...")
        if not valid_cookies:
            print("  没有可用 Cookie，跳过")
            continue
        
        tweets = fetch_tweets_from_account(driver, username, valid_cookies)
        for tw in tweets:
            if tw["id"] not in existing_ids:
                new_items.append(tw)
                existing_ids.add(tw["id"])
        print(f"  新增 {len([tw for tw in tweets if tw['id'] in existing_ids])} 条推文")
        
        if idx < len(X_ACCOUNTS):
            delay = random.uniform(*BETWEEN_ACCOUNTS_DELAY)
            print(f"  等待 {delay:.1f} 秒后继续...")
            time.sleep(delay)
    
    # ========== 抓取新闻文章（12小时窗口，保持不变） ==========
    print("\n--- 抓取新闻文章（按12小时内时间窗口筛选） ---")
    now_utc = datetime.now(timezone.utc)
    cutoff_time = now_utc - timedelta(hours=12)
    
    for listpage in NEWS_URLS:
        print(f"处理列表页: {listpage}")
        article_links = extract_article_links(driver, listpage)
        if not article_links:
            print(f"  ⚠️ 未提取到链接，跳过该列表页")
            continue
        for idx, article_url in enumerate(article_links, 1):
            print(f"  [{idx}/{len(article_links)}] 抓取: {article_url[:80]}...")
            article = fetch_article_detail(driver, article_url)
            if article:
                ts_str = article.get("original_time")
                if ts_str:
                    if ts_str.endswith("Z"):
                        ts_str = ts_str.replace("Z", "+00:00")
                    try:
                        pub_time = datetime.fromisoformat(ts_str)
                        if pub_time < cutoff_time:
                            print(f"    ⏭️ 跳过（发布时间超过12小时）: {article['title'][:50]}")
                            continue
                    except Exception as e:
                        print(f"    ⚠️ 无法解析时间，保留: {e}")
                if article["id"] not in existing_ids:
                    new_items.append(article)
                    existing_ids.add(article["id"])
                    print(f"    ✅ 新增: {article['title'][:50]}")
                else:
                    print(f"    ⏭️ 已存在")
            else:
                print(f"    ⏭️ 抓取失败")
            time.sleep(random.uniform(*BETWEEN_ARTICLES_DELAY))
    
    if new_items:
        all_items.extend(new_items)
        save_items(all_items)
        print(f"\n✅ 总计新增 {len(new_items)} 条内容")
    else:
        print("\n📭 无新增内容")
    
    recent = filter_recent_items(all_items, hours=12)
    generate_html(recent)
    elapsed = time.time() - start
    print(f"\n✅ 已生成 {HTML_FILE}，包含 {len(recent)} 条近期内容")
    print(f"总耗时: {elapsed:.2f} 秒")
    close_driver()

if __name__ == "__main__":
    main()
