#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判监测脚本（双账号随机比例 4:6，失败不切换，增量统计，支持图片，自动清理过期数据，中英文翻译）
- 顺序处理 20 个 X 账号，预先验证 Cookie 有效性，按 4:6 比例随机选择可用账号，失败不切换
- 新闻抓取部分使用 Selenium，支持 Dawn（含 Load More 点击）和 ARY News
- 抓取推文中的图片并保存到本地（原始尺寸，不替换URL），在 HTML 中显示
- 每次运行时自动删除超过12小时的旧数据和对应的图片文件
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

# ==================== 调度控制（每10分钟+随机60~120秒执行一次） ====================
LAST_RUN_FILE = "last_run.txt"

def should_run_now():
    """
    判断本次是否应该执行抓取。
    返回 (should_run, wait_seconds)
    - should_run: True 表示应该执行，False 表示跳过
    - wait_seconds: 如果 should_run 为 True 且需要等待（距离上次不足目标间隔），则等待此秒数后再开始抓取
    """
    now = datetime.now(timezone.utc)
    min_interval = 600           # 10分钟 = 600秒
    extra = random.randint(60, 120)   # 随机增量 60~120秒
    target_interval = min_interval + extra

    # 读取上次实际执行时间
    last_run = None
    if os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, "r") as f:
            try:
                last_run = datetime.fromisoformat(f.read().strip())
            except:
                pass

    if last_run is None:
        # 第一次运行，立即执行
        wait_seconds = 0
        should = True
    else:
        elapsed = (now - last_run).total_seconds()
        if elapsed >= target_interval:
            # 已达到或超过目标间隔，立即执行
            wait_seconds = 0
            should = True
        else:
            # 尚未达到目标间隔，本次跳过
            print(f"距离上次执行仅 {elapsed:.1f} 秒，未达到 {target_interval:.1f} 秒（10分钟+随机{extra}秒），本次跳过")
            should = False
            wait_seconds = 0

    if should:
        # 立即更新 last_run 时间戳，避免并发执行
        with open(LAST_RUN_FILE, "w") as f:
            f.write(now.isoformat())
        if wait_seconds > 0:
            print(f"距离上次执行未满目标间隔，等待 {wait_seconds:.1f} 秒后开始抓取...")
    return should, wait_seconds

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

# Dawn "Load More" 配置
DAWN_MAX_LOAD_MORE_CLICKS = 10   # 最多点击10次“加载更多”
DAWN_LOAD_MORE_WAIT = 2          # 每次点击后等待秒数

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

# ==================== 验证 Cookie 有效性 ====================
def check_cookie_valid(driver, account):
    """验证指定账号的 Cookie 是否有效（能正常访问 X 首页，不跳转到登录页）"""
    if not inject_cookies(driver, account):
        return False
    try:
        driver.get("https://x.com/home")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        current_url = driver.current_url
        if "/login" in current_url:
            print(f"⚠️ 账号{account} Cookie 无效（跳转到登录页）")
            return False
        if "Log in" in driver.page_source[:2000] or "Sign up" in driver.page_source[:2000]:
            print(f"⚠️ 账号{account} Cookie 可能无效（页面仍显示登录按钮）")
            return False
        print(f"✅ 账号{account} Cookie 有效")
        return True
    except Exception as e:
        print(f"❌ 验证账号{account} Cookie 时出错: {e}")
        return False

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

# ==================== 下载图片 ====================
def download_image(img_url, save_dir=IMAGES_DIR):
    """下载图片并返回本地文件名，失败返回 None"""
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

# ==================== 抓取推文 ====================
def fetch_tweets_from_account(username, driver, account=1, max_tweets=MAX_TWEETS_PER_ACCOUNT, retry=True):
    """使用指定账号的 Cookie 抓取推文（包括图片、翻译），自动点击 "Show more"，失败时可重试一次"""
    url = f"https://x.com/{username}"
    for attempt in range(1, 3 if retry else 1):
        try:
            driver.get(url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"]'))
            )
            time.sleep(1)
            
            # ========== 新增：点击每个推文中的 "Show more" 按钮 ==========
            # 获取所有推文 article 元素（Selenium WebElement 列表）
            article_elements = driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
            # 只处理前 max_tweets 个
            for article_elem in article_elements[:max_tweets]:
                try:
                    # 查找 "Show more" 或 "more" 按钮（不区分大小写）
                    show_more_btn = article_elem.find_element(By.XPATH, './/button[contains(text(), "Show more") or contains(text(), "more")]')
                    if show_more_btn.is_displayed() and show_more_btn.is_enabled():
                        # 使用 JavaScript 点击，避免元素被遮挡
                        driver.execute_script("arguments[0].click();", show_more_btn)
                        time.sleep(0.5)  # 等待内容展开
                except:
                    pass  # 没有 "Show more" 按钮，正常继续
            # ========================================================
            
            # 重新获取页面源码（因为点击后 DOM 已更新）
            soup = BeautifulSoup(driver.page_source, "html.parser")
            articles = soup.find_all('article', attrs={'data-testid': 'tweet'})
            
            tweets = []
            for art in articles[:max_tweets]:
                try:
                    # 获取推文文本（已展开）
                    text_div = art.find('div', {'data-testid': 'tweetText'})
                    text = text_div.get_text(strip=True) if text_div else ""
                    if len(text) > 500:
                        text = text[:497] + "..."
                    
                    translated_text = translate_text(text)
                    
                    # 提取图片（与原来完全一致）
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
                    
                    # 提取推文链接和 ID
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

# ==================== 新闻抓取函数（支持 Dawn Load More，允许 /cartoon/） ====================
def fetch_article_detail(driver, article_url):
    """抓取单篇文章的标题和发布时间，并翻译标题。优先等待动态内容加载。"""
    try:
        driver.get(article_url)
        
        # --- 关键修改点：明确等待标题元素加载完成（仅对 Dawn 有效，失败则跳过）---
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'h2.story__title'))
            )
            time.sleep(1)
        except Exception:
            pass
        # --------------------------------------------
        
        # 检查页面是否被屏蔽
        page_title = driver.title.lower()
        if "opt out" in page_title or "privacy" in page_title:
            print(f"  ⚠️ 页面可能被屏蔽，跳过: {article_url[:80]}")
            return None
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        title = None
        
        # --- 策略 1: 针对 Dawn 网站的精准选择器 ---
        title_elem = soup.select_one('h2.story__title a.story__link')
        if title_elem:
            title = title_elem.get_text(strip=True)
            print(f"    📝 [Dawn Selector] 提取到标题: {title[:50]}")
        
        # --- 策略 2: 从 JSON-LD 结构化数据中提取标题 ---
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
        
        # --- 策略 3: 从 Open Graph 中提取标题 ---
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
                print(f"    📝 [og:title] 提取到标题: {title[:50]}")
        
        # --- 策略 4: 从 <h1> 标签中提取标题 ---
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
                print(f"    📝 [<h1>] 提取到标题: {title[:50]}")
        
        # --- 策略 5: 从 <title> 标签中提取标题（降级方案）---
        if not title:
            t = soup.find("title")
            if t:
                candidate = t.get_text(strip=True)
                # 排除一些通用的、非文章标题的内容
                if not any(x in candidate.lower() for x in ["opt out", "privacy", "share", "dawn.com", "ary news"]):
                    title = candidate
                    print(f"    📝 [<title>] 提取到标题: {title[:50]}")
        
        # --- 策略 6: 最终兜底 ---
        if not title:
            title = "无标题"
            print(f"    ⚠️ 未能提取到标题，使用默认值")
        
        # 翻译标题
        translated_title = translate_text(title)
        
        # 提取发布时间（原逻辑保持不变）
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
    
    # ========== 其他网站（备用） ==========
    driver.get(listpage_url)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
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
        if any(x in href for x in ['/news/', '/story/', '/article/']):
            all_links.add(full_url)
    result = list(all_links)
    print(f"  从 {listpage_url} 提取到 {len(result)} 个文章链接")
    return result

def fetch_article_detail(driver, article_url):
    """抓取单篇文章的标题和发布时间，并翻译标题。优先等待动态内容加载。"""
    try:
        driver.get(article_url)
        
        # --- 关键修改点：明确等待标题元素加载完成 ---
        # 等待 h2.story__title 元素出现，最多等待15秒
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'h2.story__title'))
        )
        time.sleep(1)  # 等待元素稳定
    except Exception:
        # 非 Dawn 网站（如 ARY News）没有此元素，忽略超时，继续执行
        pass
        # --------------------------------------------
        
        # 检查页面是否被屏蔽
        page_title = driver.title.lower()
        if "opt out" in page_title or "privacy" in page_title:
            print(f"  ⚠️ 页面可能被屏蔽，跳过: {article_url[:80]}")
            return None
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        title = None
        
        # --- 策略 1: 针对 Dawn 网站的精准选择器 ---
        title_elem = soup.select_one('h2.story__title a.story__link')
        if title_elem:
            title = title_elem.get_text(strip=True)
            print(f"    📝 [Dawn Selector] 提取到标题: {title[:50]}")
        
        # --- 策略 2: 从 JSON-LD 结构化数据中提取标题 ---
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
        
        # --- 策略 3: 从 Open Graph 中提取标题 ---
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
                print(f"    📝 [og:title] 提取到标题: {title[:50]}")
        
        # --- 策略 4: 从 <h1> 标签中提取标题 ---
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
                print(f"    📝 [<h1>] 提取到标题: {title[:50]}")
        
        # --- 策略 5: 从 <title> 标签中提取标题（降级方案）---
        if not title:
            t = soup.find("title")
            if t:
                candidate = t.get_text(strip=True)
                # 排除一些通用的、非文章标题的内容
                if not any(x in candidate.lower() for x in ["opt out", "privacy", "share", "dawn.com", "ary news"]):
                    title = candidate
                    print(f"    📝 [<title>] 提取到标题: {title[:50]}")
        
        # --- 策略 6: 最终兜底 ---
        if not title:
            title = "无标题"
            print(f"    ⚠️ 未能提取到标题，使用默认值")
        
        # 翻译标题
        translated_title = translate_text(title)
        
        # 提取发布时间（原逻辑保持不变）
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

# ==================== 清理过期数据 ====================
def clean_old_data(hours=12):
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

# ==================== 生成 HTML ====================
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
        📊 显示最近12小时内数据 | 页面每10分钟自动刷新<br>
        {change_msg}
    </div>
    <h2>🐦 X 推文 ({tweet_count})</h2>
    {tweets_html}
    <h2>📰 新闻文章 ({article_count})</h2>
    {articles_html}
    <div class="footer">
        <hr>
        <p>数据来源：X平台 + Dawn / ARY News | 自动抓取部署于 GitHub Actions | 淘汰超过12小时的内容 | 英文自动翻译为中文</p>
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
    # 调度控制：检查是否应该执行本次抓取（最小间隔10分钟+随机60~120秒）
    should_run, wait_sec = should_run_now()
    if not should_run:
        print("跳过本次执行，未达到最小间隔10分钟+随机增量")
        return
    if wait_sec > 0:
        print(f"距离上次执行未满目标间隔，等待 {wait_sec:.1f} 秒后开始抓取...")
        time.sleep(wait_sec)

    print(f"{datetime.now()} 开始抓取（支持图片、自动清理、中英文翻译）...")
    
    # 清理超过12小时的旧数据和图片
    clean_old_data(hours=12)
    
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
    
    # 预先验证两个 Cookie 的有效性
    valid1 = False
    valid2 = False
    if account1_available:
        valid1 = check_cookie_valid(driver, 1)
    if account2_available:
        valid2 = check_cookie_valid(driver, 2)
    
    account_usage = {1: 0, 2: 0}
    
    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    new_items = []
    
    if x_enabled:
        print("\n--- 抓取 X 平台推文（按4:6比例随机选择可用账号，失败不切换） ---")
        for idx, username in enumerate(X_ACCOUNTS, 1):
            available = []
            if valid1:
                available.append(1)
            if valid2:
                available.append(2)
            
            if not available:
                print(f"[{idx}/{len(X_ACCOUNTS)}] 跳过 @{username}：没有可用的 Cookie")
                continue
            
            if len(available) == 2:
                chosen_account = 1 if random.random() < 0.4 else 2
            else:
                chosen_account = available[0]
            
            account_usage[chosen_account] += 1
            
            print(f"[{idx}/{len(X_ACCOUNTS)}] 抓取 @{username} (选用账号{chosen_account}) ...")
            if not inject_cookies(driver, account=chosen_account):
                print(f"  ❌ 无法注入账号{chosen_account}的 Cookie，跳过 @{username}")
                continue
            
            tweets = fetch_tweets_from_account(username, driver, account=chosen_account, retry=True)
            
            for tw in tweets:
                if tw["id"] not in existing_ids:
                    new_items.append(tw)
                    existing_ids.add(tw["id"])
            
            if idx < len(X_ACCOUNTS):
                delay = random.uniform(*BETWEEN_ACCOUNTS_DELAY)
                time.sleep(delay)
    
    print("\n📊 本次运行账号使用统计:")
    print(f"   账号1 被选中 {account_usage[1]} 次")
    print(f"   账号2 被选中 {account_usage[2]} 次")
    total_usage = account_usage[1] + account_usage[2]
    if total_usage > 0:
        print(f"   实际比例: 账号1 {account_usage[1]/total_usage*100:.1f}% , 账号2 {account_usage[2]/total_usage*100:.1f}%")
    
    # ========== 新闻抓取（支持 Load More，按时间窗口筛选） ==========
    print("\n--- 抓取新闻文章（按12小时内时间窗口筛选） ---")
    now_utc = datetime.now(timezone.utc)
    cutoff_time = now_utc - timedelta(hours=12)
    
    for listpage in NEWS_URLS:
        print(f"处理列表页: {listpage}")
        article_links = extract_article_links(driver, listpage)  # 传入 driver
        if not article_links:
            print(f"  ⚠️ 未提取到链接，跳过该列表页")
            continue
        for idx, article_url in enumerate(article_links, 1):
            print(f"  [{idx}/{len(article_links)}] 抓取: {article_url[:80]}...")
            article = fetch_article_detail(driver, article_url)
            if article:
                # 解析发布时间并判断是否在12小时内
                ts_str = article.get("original_time")
                if ts_str:
                    if ts_str.endswith("Z"):
                        ts_str = ts_str.replace("Z", "+00:00")
                    try:
                        pub_time = datetime.fromisoformat(ts_str)
                        # 如果发布时间早于12小时前，则丢弃
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
