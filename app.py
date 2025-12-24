import os
import time
import zipfile
import io
import sys
import asyncio
from datetime import datetime
import streamlit as st
import cloudinary
import cloudinary.uploader
from pyairtable import Api
import subprocess
import os

# Check if chromium is installed, if not, install it
# Check if chromium and fonts are installed, if not, install them
@st.cache_resource
def install_playwright_browsers():
    try:
        # We no longer run apt-get here because packages.txt handles it
        # Just install the Playwright chromium binary
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except Exception as e:
        st.error(f"Error installing playwright: {e}")


# Call the function
install_playwright_browsers()
# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv not installed, will use system env variables

# Windows-specific fix for Python 3.13 + Playwright subprocess error
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright, ViewportSize

# --- CONFIGURATION ---
UPLOAD_FOLDER = 'static/captures'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# Helper function to get config from Streamlit secrets or environment variables
def get_config(key, default=None):
    """Get configuration from Streamlit secrets first, then environment variables."""
    try:
        return st.secrets.get(key, os.getenv(key, default))
    except:
        return os.getenv(key, default)


# Cloudinary Configuration
CLOUDINARY_CLOUD_NAME = get_config('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = get_config('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = get_config('CLOUDINARY_API_SECRET')

# Airtable Configuration
AIRTABLE_API_KEY = get_config('AIRTABLE_API_KEY')
AIRTABLE_BASE_ID = get_config('AIRTABLE_BASE_ID')
AIRTABLE_TABLE_NAME = get_config('AIRTABLE_TABLE_NAME')

# Configure Cloudinary only if credentials are available
if all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )

# Fix SSL certificate verification issues
import ssl
import certifi
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

st.set_page_config(page_title="Banner Capture", layout="wide")


# --- CLOUDINARY UPLOAD ---

def upload_to_cloudinary(file_path, country_code, mode, slide_num):
    """Upload image to Cloudinary and return the URL."""
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
        st.warning("⚠️ Cloudinary credentials not configured. Please set them in .env file or Streamlit secrets.")
        return None, None

    try:
        import hashlib
        import base64

        # Generate timestamp
        timestamp = int(time.time())

        # Prepare upload parameters
        folder_name = f"lg_banners/{country_code}/{mode}"
        public_id = f"{country_code}_{mode}_hero_{slide_num}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Method 1: Try using cloudinary SDK with proper config
        try:
            response = cloudinary.uploader.upload(
                file_path,
                folder=folder_name,
                public_id=public_id,
                resource_type="image",
                overwrite=True,
                use_filename=False
            )
            return response.get('secure_url'), response.get('public_id')
        except Exception as sdk_error:
            # Method 2: Fallback to direct API call with proper signature
            import requests

            # Create signature for authentication
            params_to_sign = f"folder={folder_name}&public_id={public_id}&timestamp={timestamp}{CLOUDINARY_API_SECRET}"
            signature = hashlib.sha1(params_to_sign.encode('utf-8')).hexdigest()

            url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"

            with open(file_path, 'rb') as f:
                files = {'file': f}
                data = {
                    'api_key': CLOUDINARY_API_KEY,
                    'timestamp': timestamp,
                    'signature': signature,
                    'folder': folder_name,
                    'public_id': public_id
                }

                response = requests.post(url, files=files, data=data, verify=False)
                response.raise_for_status()
                result = response.json()

                return result.get('secure_url'), result.get('public_id')

    except Exception as e:
        st.error(f"❌ Cloudinary upload failed: {str(e)}")
        return None, None


# --- AIRTABLE INTEGRATION ---

def save_to_airtable(country_code, mode, slide_num, image_url, cloudinary_id, capture_date):
    """Save capture metadata to Airtable."""
    if not all([AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME]):
        st.warning("⚠️ Airtable credentials not configured. Please set them in .env file or Streamlit secrets.")
        return None

    try:
        # Method 1: Try using pyairtable with SSL fix
        try:
            api = Api(AIRTABLE_API_KEY)
            table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

            record = {
                "Country": country_code.upper(),
                "Mode": mode.capitalize(),
                "Slide Number": slide_num,
                "Image URL": image_url,
                "Cloudinary ID": cloudinary_id,
                "Capture Date": capture_date,
                "Timestamp": datetime.now().isoformat(),
            }

            created_record = table.create(record)
            return created_record['id']

        except Exception as pyairtable_error:
            # Method 2: Fallback to direct requests API call
            import requests

            url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"

            headers = {
                "Authorization": f"Bearer {AIRTABLE_API_KEY}",
                "Content-Type": "application/json"
            }

            data = {
                "fields": {
                    "Country": country_code.upper(),
                    "Mode": mode.capitalize(),
                    "Slide Number": slide_num,
                    "Image URL": image_url,
                    "Cloudinary ID": cloudinary_id,
                    "Capture Date": capture_date,
                    "Timestamp": datetime.now().isoformat(),
                }
            }

            response = requests.post(url, json=data, headers=headers, verify=False)
            response.raise_for_status()
            result = response.json()

            return result.get('id')

    except Exception as e:
        st.error(f"❌ Airtable save failed: {str(e)}")
        return None


# --- CORE CAPTURE LOGIC (Enhanced with Hero Detection) ---

def apply_clean_styles(page_obj):
    """Comprehensive CSS cleanup."""
    page_obj.evaluate("""
        document.querySelectorAll('.c-notification-banner').forEach(el => el.remove());
        const style = document.createElement('style');
        style.innerHTML = `
            /* ADDED: .c-membership-popup to target the registration overlay */
            [class*="chat"], [id*="chat"], [class*="proactive"], 
        .alk-container, #genesys-chat, .genesys-messenger,
        .floating-button-portal, #WAButton, .embeddedServiceHelpButton,
        .c-pop-toast__container, .onetrust-pc-dark-filter, #onetrust-consent-sdk,
        .c-membership-popup, 
        [class*="cloud-shoplive"], [class*="csl-"], [class*="svelte-"], 
        .l-cookie-teaser, .c-cookie-settings, .LiveMiniPreview,
        .c-notification-banner, .c-notification-banner *, .c-notification-banner__wrap,
        .open-button, .js-video-pause, .js-video-play, [aria-label*="Pausar"], [aria-label*="video"]
            { display: none !important; visibility: hidden !important; opacity: 0 !important; pointer-events: none !important; }
        `;
        document.head.appendChild(style);

        const hideSelectors = ['.c-header', '.navigation', '.iw_viewport-wrapper > header', '.al-quick-btn__quickbtn', '.al-quick-btn__topbtn'];
        hideSelectors.forEach(s => {
            document.querySelectorAll(s).forEach(el => el.style.setProperty('display', 'none', 'important'));
        });

        const opacitySelectors = ['.cmp-carousel__indicators', '.cmp-carousel__actions', '.c-carousel-controls'];
        opacitySelectors.forEach(s => {
            document.querySelectorAll(s).forEach(el => el.style.setProperty('opacity', '0', 'important'));
        });
    """)


def find_hero_carousel(page, log_callback=None):
    """
    Intelligently identify the FIRST/MAIN hero banner carousel on LG.com pages.
    Filters out notification banners and other non-hero carousels.
    """

    def log(message):
        if log_callback:
            log_callback(message)

    log("🔍 Detecting hero carousel...")

    # ADDED: Centralized list of wrappers to exclude
    excluded_wrappers = ".c-notification-banner, .l-cookie-teaser, .c-membership-popup"

    # Strategy 1: Look for carousel in common hero/main sections
    hero_selectors = [
        "main .cmp-carousel",
        ".main-content .cmp-carousel",
        ".hero-section .cmp-carousel",
        ".c-hero-section .cmp-carousel",
        "[class*='hero'] .cmp-carousel",
        ".content .cmp-carousel",
        "section .cmp-carousel",
    ]

    hero_carousel = None
    for selector in hero_selectors:
        try:
            # ADDED: Changed to query_selector_all to allow skipping excluded carousels
            elements = page.query_selector_all(selector)
            for element in elements:
                # ADDED: Check if this specific element is inside an excluded wrapper
                is_in_excluded = element.evaluate(f"el => !!el.closest('{excluded_wrappers}')")
                if is_in_excluded:
                    continue

                # Original validation logic
                indicators = element.query_selector_all(".cmp-carousel__indicator")
                if len(indicators) > 0:
                    bbox = element.bounding_box()
                    if bbox and bbox['height'] >= 300:
                        log(f"✅ Found hero carousel using: {selector}")
                        hero_carousel = element
                        break
            if hero_carousel:
                break
        except Exception:
            continue

    if not hero_carousel:
        log("⚠️ Could not find hero carousel with specific selectors, using advanced scoring...")
        try:
            all_carousels = page.query_selector_all(".cmp-carousel")
            candidates = []
            viewport_size = page.viewport_size
            viewport_width = viewport_size['width'] if viewport_size else 1280

            for idx, carousel in enumerate(all_carousels):
                # ADDED: Explicitly skip carousels inside excluded notification/cookie wrappers
                is_in_excluded = carousel.evaluate(f"el => !!el.closest('{excluded_wrappers}')")
                if is_in_excluded:
                    log(f"   Carousel {idx}: SKIPPED (inside {excluded_wrappers})")
                    continue

                indicators = carousel.query_selector_all(".cmp-carousel__indicator")
                if len(indicators) == 0:
                    continue

                bbox = carousel.bounding_box()
                if not bbox:
                    continue

                # FILTER 1: Skip notification/alert banners
                if bbox['height'] < 200:
                    log(f"   Carousel {idx}: SKIPPED (too short: {bbox['height']:.0f}px)")
                    continue

                # FILTER 2: Skip narrow carousels
                if bbox['width'] < viewport_width * 0.5:
                    log(f"   Carousel {idx}: SKIPPED (too narrow: {bbox['width']:.0f}px)")
                    continue

                # Look for hero banner content inside
                has_hero_banner = carousel.query_selector(".c-hero-banner") is not None
                has_hero_image = carousel.query_selector(".c-image__item, .cmp-image") is not None

                # FILTER 3: Skip if it looks like legal/notification content (Original List Preserved)
                try:
                    carousel_text = carousel.inner_text().lower()
                    notification_keywords = [
                        'cookie', 'クッキー', 'プライバシー', 'privacy', 'notice',
                        'お知らせ', '利用規約', '特定商取引', 'オンラインショップ',
                        'terms', 'conditions', '規約', '改正'
                    ]
                    if any(keyword in carousel_text for keyword in notification_keywords):
                        log(f"   Carousel {idx}: SKIPPED (notification/legal content detected)")
                        continue
                except:
                    pass

                # Scoring logic (Original Scores Preserved)
                score = 0
                if has_hero_banner:
                    score += 100
                if has_hero_image:
                    score += 50

                area = bbox['width'] * bbox['height']
                if area > 500000:
                    score += 30

                if bbox['height'] > 400:
                    score += 50
                elif bbox['height'] > 300:
                    score += 30
                elif bbox['height'] > 200:
                    score += 10

                # Position Scoring
                if 100 < bbox['y'] < 600:
                    score += 25
                elif 50 < bbox['y'] < 100:
                    score -= 20
                elif bbox['y'] < 50:
                    score -= 100

                # Width Scoring
                if bbox['width'] > viewport_width * 0.9:
                    score += 20
                elif bbox['width'] > viewport_width * 0.8:
                    score += 15

                candidates.append({
                    'carousel': carousel,
                    'score': score,
                    'position': bbox['y'],
                    'height': bbox['height'],
                    'size': area,
                    'has_hero': has_hero_banner,
                    'index': idx
                })

                log(f"   Carousel {idx}: score={score}, pos={bbox['y']:.0f}px, height={bbox['height']:.0f}px, size={area:.0f}, hero={has_hero_banner}")

            if candidates:
                candidates.sort(key=lambda x: x['score'], reverse=True)
                best = candidates[0]

                if best['score'] > 0:
                    hero_carousel = best['carousel']
                    log(f"✅ Selected carousel {best['index']} (score: {best['score']})")
                else:
                    log(f"❌ No suitable carousel found (best score: {best['score']})")

        except Exception as e:
            log(f"❌ Error in advanced detection: {str(e)}")

    return hero_carousel


def capture_hero_banners(url, country_code, mode='desktop', log_callback=None, upload_to_cloud=False):
    def log(message):
        if log_callback:
            log_callback(message)

    size: ViewportSize = {'width': 1920, 'height': 720} if mode == 'desktop' else {'width': 360, 'height': 480}

    session_folder_name = f"{country_code}_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_path = os.path.join(UPLOAD_FOLDER, session_folder_name)
    os.makedirs(session_path, exist_ok=True)

    with sync_playwright() as p:
        log("🚀 Launching browser...")
        # Add these specific flags for Cloud/Linux environments
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",  # Crucial for Linux containers
                "--disable-setuid-sandbox",  # Crucial for Linux containers
                "--disable-dev-shm-usage",  # Prevents crashes in low-memory environments
                "--disable-gpu"  # Servers don't have GPUs
            ]
        )
        context = browser.new_context(viewport=size)
        page = context.new_page()

        def block_chat_requests(route):
            url_str = route.request.url.lower()
            chat_keywords = ["genesys", "liveperson", "salesforceliveagent", "adobe-privacy", "chatbot",
                             "proactive-chat"]
            if any(key in url_str for key in chat_keywords):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", block_chat_requests)

        try:
            log(f"🌐 Navigating to {url}...")
            page.goto(url, wait_until="load", timeout=90000)

            # Cookie Acceptance
            try:
                accept_btn = page.locator("#onetrust-accept-btn-handler")
                if accept_btn.is_visible(timeout=5000):
                    log("🍪 Accepting cookies...")
                    accept_btn.click()
                    time.sleep(2)
            except:
                pass

            # Wait for any carousel to load
            page.wait_for_selector("main .cmp-carousel, .main .cmp-carousel, #contents .cmp-carousel", timeout=30000)

            # Find the hero carousel specifically
            hero_carousel = find_hero_carousel(page, log_callback)

            if not hero_carousel:
                log("❌ Could not identify hero carousel")
                return

            try:
                pause_btn = hero_carousel.query_selector(".js-carousel-pause")
                if pause_btn and pause_btn.is_visible():
                    log("⏸️ Pausing carousel autoplay...")
                    pause_btn.click(force=True)
                    time.sleep(1)
            except:
                pass

            # Get indicators from ONLY the hero carousel
            indicators = hero_carousel.query_selector_all(".cmp-carousel__indicator")
            num_slides = len(indicators)
            log(f"📸 Found {num_slides} banners in HERO carousel.")

            if num_slides > 0:
                log("🔄 Resetting to first slide...")
                indicators[0].click(force=True)
                time.sleep(2)

            for i in range(num_slides):
                log(f"📷 Processing slide {i + 1} of {num_slides}...")

                # Re-query indicators
                indicators = hero_carousel.query_selector_all(".cmp-carousel__indicator")
                if i >= len(indicators):
                    continue

                # 1. DEFINE THE SELECTOR FIRST (Fixes the local variable error)
                active_slide_selector = f".cmp-carousel__item.swiper-slide-active[data-swiper-slide-index='{i}']"

                # 2. CLICK THE INDICATOR
                indicators[i].click(force=True)

                # 3. MINIMAL LOAD FIX: Force lazy-load to trigger by scrolling & waiting for pixels
                try:
                    # Remove the lazy attribute and scroll to trigger the download
                    page.evaluate(f"""
                                    () => {{
                                        const slide = document.querySelector("{active_slide_selector}");
                                        if (slide) {{
                                            const img = slide.querySelector('img');
                                            if (img) img.removeAttribute('loading');
                                            slide.scrollIntoView();
                                        }}
                                    }}
                                """)

                    # Wait for the image to have actual physical pixels (naturalWidth)
                    page.wait_for_function(f"""
                                    () => {{
                                        const img = document.querySelector("{active_slide_selector} img");
                                        return !img || (img.complete && img.naturalWidth > 0);
                                    }}
                                """, timeout=10000)
                except:
                    pass  # Don't let a timeout break the app

                # Apply styles immediately after click
                apply_clean_styles(page)

                # Get the data-swiper-slide-index for the active slide
                active_slide_selector = f".cmp-carousel__item.swiper-slide-active[data-swiper-slide-index='{i}']"

                try:
                    page.wait_for_selector(active_slide_selector, timeout=15000)
                except:
                    active_slide_selector = ".cmp-carousel__item.swiper-slide-active"
                    page.wait_for_selector(active_slide_selector, timeout=15000)

                # Wait for image/background to load and animations to complete
                try:
                    page.wait_for_function(f"""
                        () => {{
                            const slide = document.querySelector('{active_slide_selector}');
                            if (!slide) return false;

                            // Check if images are loaded
                            const img = slide.querySelector('img');
                            const imgReady = img ? (img.complete && img.naturalWidth > 0) : false;

                            // Check if background images are loaded
                            const bgDiv = slide.querySelector('.c-image__item, .cmp-image, .c-hero-banner');
                            const bgStyle = bgDiv ? window.getComputedStyle(bgDiv).backgroundImage : "";
                            const bgReady = bgStyle && bgStyle !== 'none' && bgStyle !== 'initial';

                            // Check if slide transition animation is complete
                            const transform = window.getComputedStyle(slide.parentElement).transform;
                            const isStable = transform !== 'none'; // Has positioning applied

                            return (imgReady || bgReady) && isStable;
                        }}
                    """, timeout=15000)
                except:
                    # Fallback to short sleep if wait fails
                    time.sleep(1.0)

                filename = f"{country_code}_{mode}_hero_{i + 1}.png"
                filepath = os.path.join(session_path, filename)

                # Try multiple selectors for the banner element
                element = None
                banner_selectors = [
                    f"{active_slide_selector} .c-hero-banner",
                    f"{active_slide_selector} .cmp-image",
                    f"{active_slide_selector} .c-image",
                    active_slide_selector
                ]

                for selector in banner_selectors:
                    element = page.query_selector(selector)
                    if element:
                        break

                if element:
                    element.screenshot(path=filepath)
                    log(f"✅ Captured: {filename}")

                    # Upload to Cloudinary and save to Airtable if enabled
                    cloudinary_url = None
                    cloudinary_id = None
                    airtable_id = None

                    if upload_to_cloud:
                        log(f"☁️ Uploading to Cloudinary...")
                        cloudinary_url, cloudinary_id = upload_to_cloudinary(
                            filepath, country_code, mode, i + 1
                        )

                        if cloudinary_url:
                            log(f"✅ Cloudinary upload successful")
                            log(f"💾 Saving metadata to Airtable...")

                            airtable_id = save_to_airtable(
                                country_code,
                                mode,
                                i + 1,
                                cloudinary_url,
                                cloudinary_id,
                                datetime.now().strftime('%Y-%m-%d')
                            )

                            if airtable_id:
                                log(f"✅ Airtable record created: {airtable_id}")

                    yield filepath, i + 1, cloudinary_url, airtable_id
                else:
                    log(f"⚠️ Could not find banner element for slide {i + 1}")

        except Exception as e:
            log(f"❌ Error: {str(e)}")
        finally:
            log("🔒 Closing browser.")
            browser.close()


# --- STREAMLIT UI ---

def main():
    st.title("📸 LG Hero Banner Capture")

    # Display configuration status
    with st.expander("⚙️ Configuration Status", expanded=False):
        cloudinary_configured = all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET])
        airtable_configured = all([AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME])

        st.write("**Cloudinary:**", "✅ Configured" if cloudinary_configured else "❌ Not configured")
        st.write("**Airtable:**", "✅ Configured" if airtable_configured else "❌ Not configured")

        if not cloudinary_configured or not airtable_configured:
            st.info("💡 Set credentials in `.env` file locally or in Streamlit Cloud secrets")

    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []

    with st.sidebar:
        st.header("Settings")

        # Add Airtable Debug Test Button
        if st.button("🔍 Test Airtable Connection"):
            try:
                import requests

                st.write("**Testing GET (Read):**")
                url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
                headers = {
                    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
                }
                response = requests.get(url, headers=headers, verify=False)

                st.write(f"- Status Code: {response.status_code}")
                st.write(f"- Base ID: `{AIRTABLE_BASE_ID}`")
                st.write(f"- Table Name: `{AIRTABLE_TABLE_NAME}`")
                st.write(f"- Token starts with: `{AIRTABLE_API_KEY[:7]}...`")

                if response.status_code == 200:
                    st.success("✅ READ access works!")
                else:
                    st.error(f"❌ READ failed: {response.text}")

                st.divider()
                st.write("**Testing POST (Write):**")

                # Test creating a record
                test_data = {
                    "fields": {
                        "Country": "TEST",
                        "Mode": "Test",
                        "Slide Number": 1,
                        "Image URL": "https://example.com/test.png",
                        "Cloudinary ID": "test_id",
                        "Capture Date": datetime.now().strftime('%Y-%m-%d'),
                        "Timestamp": datetime.now().isoformat()
                    }
                }

                write_response = requests.post(url, json=test_data, headers=headers, verify=False)

                st.write(f"- Status Code: {write_response.status_code}")

                if write_response.status_code in [200, 201]:
                    st.success("✅ WRITE access works!")
                    st.write("Test record created successfully!")
                    st.json(write_response.json())
                else:
                    st.error(f"❌ WRITE failed: {write_response.text}")
                    st.write("**Full response:**")
                    st.code(write_response.text)

            except Exception as e:
                st.error(f"❌ Test failed: {str(e)}")

        st.divider()

        countries = [
            ("au", "Australia (AU)"),
            ("uk", "United Kingdom (UK)"),
            ("ca_en", "Canada (CA_EN)"),
            ("ca_fr", "Canada (CA_FR)"),
            ("fr", "France (FR)"),
            ("de", "Germany (DE)"),
            ("it", "Italy (IT)"),
            ("es", "Spain (ES)"),
            ("nl", "Netherlands (NL)"),
            ("sw", "Sweden (SW)"),
            ("pt", "Portugal (PT)"),
            ("hu", "Hungary (HU)"),
            ("pl", "Poland (PL)"),
            ("at", "Austria (AT)"),
            ("cz", "Czech Republic (CZ)"),
            ("mx", "Mexico (MX)"),
            ("br", "Brazil (BR)"),
            ("ar", "Argentina (AR)"),
            ("cl", "Chile (CL)"),
            ("co", "Colombia (CO)"),
            ("pe", "Peru (PE)"),
            ("pa", "Panama (PA)"),
            ("jp", "Japan (JP)"),
            ("hk", "Hong Kong (HK)"),
            ("sg", "Singapore (SG)"),
            ("my", "Malaysia (MY)"),
            ("th", "Thailand (TH)"),
            ("vn", "Vietnam (VN)"),
            ("ph", "Philippines (PH)"),
            ("in", "Indonesia (IN)"),
            ("tw", "Taiwan (TW)")
        ]

        country_labels = [label for _, label in countries]
        country_codes = [code for code, _ in countries]

        default_index = country_codes.index("jp")

        selected_country = st.selectbox(
            "Country/Region",
            options=country_labels,
            index=default_index
        )

        site = country_codes[country_labels.index(selected_country)]
        mode = st.selectbox("View Mode", options=["desktop", "mobile"])

        st.divider()
        st.subheader("☁️ Airtable Upload")

        # Check if credentials are configured
        cloudinary_ready = all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET])
        airtable_ready = all([AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME])

        upload_enabled = st.checkbox(
            "Upload to Cloudinary & Airtable",
            value=False,
            disabled=not (cloudinary_ready and airtable_ready)
        )

        if not (cloudinary_ready and airtable_ready):
            st.warning("⚠️ Cloud upload disabled: Configure credentials first")

        st.divider()
        run_btn = st.button("Start Capture", type="primary", use_container_width=True)

        st.divider()
        st.subheader("Activity Log")
        log_placeholder = st.empty()

    if run_btn:
        st.session_state.log_messages = []
        captured_files = []
        url = f"https://www.lg.com/{site}/"

        def add_log(message):
            msg = f"`{datetime.now().strftime('%H:%M:%S')}` {message}"
            st.session_state.log_messages.append(msg)
            log_placeholder.markdown("\n\n".join(st.session_state.log_messages[::-1]))

        st.subheader(f"Results: {site.upper()} ({mode})")
        cols = st.columns(3)

        # Run the generator
        for idx, result in enumerate(
                capture_hero_banners(url, site, mode, log_callback=add_log, upload_to_cloud=upload_enabled)):
            img_path, slide_num = result[0], result[1]
            cloudinary_url = result[2] if len(result) > 2 else None
            airtable_id = result[3] if len(result) > 3 else None

            captured_files.append(img_path)

            with cols[idx % 3]:
                st.image(img_path, caption=f"Slide {slide_num}")
                if cloudinary_url:
                    st.caption(f"☁️ [View on Cloudinary]({cloudinary_url})")

        if captured_files:
            st.divider()

            # Create ZIP for download
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zf:
                for fpath in captured_files:
                    zf.write(fpath, os.path.basename(fpath))

            st.download_button(
                label="📥 Download All Banners (ZIP)",
                data=zip_buffer.getvalue(),
                file_name=f"banners_{site}_{mode}_{datetime.now().strftime('%Y%m%d')}.zip",
                mime="application/zip",
                use_container_width=True
            )
            st.success(f"✅ Capture complete! {len(captured_files)} images saved.")

            if upload_enabled:
                st.info(f"☁️ {len(captured_files)} images uploaded to Cloudinary and logged in Airtable")
        else:
            st.warning("No banners were captured. Check the activity log for details.")


if __name__ == "__main__":
    main()