import subprocess
import sys
import time
from urllib.parse import urlparse
import pandas as pd
import streamlit as st
from playwright.sync_api import sync_playwright

# --- Streamlit Cloud Auto-Installation ---
@st.cache_resource
def setup_playwright():
    """Ensure Playwright Chromium browser and system dependencies are installed on Cloud boot."""
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"], check=True)
    except Exception as e:
        print(f"Playwright setup info: {e}")

# Run setup at application launch
setup_playwright()


def clean_url(url):
    """Normalizes URLs by stripping protocol, www, trailing slashes, and query params."""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    parsed = urlparse(url)
    netloc = parsed.netloc.lower().replace('www.', '')
    path = parsed.path.rstrip('/')
    return f"{netloc}{path}"


def crawl_with_playwright(target_urls, source_urls):
    """
    Launches a Chromium browser via Playwright to render JavaScript DOM,
    extract internal links, and match them against target URLs.
    """
    target_map = {clean_url(u): u.strip() for u in target_urls if u.strip()}
    target_cleans = set(target_map.keys())

    inbound_db = {clean: [] for clean in target_cleans}
    audit_logs = []

    progress_bar = st.progress(0)
    status_text = st.empty()
    total_sources = len(source_urls)

    with sync_playwright() as p:
        # Container-friendly launch flags required for Streamlit Cloud & Docker
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for idx, source_url in enumerate(source_urls):
            source_url = source_url.strip()
            if not source_url:
                continue

            status_text.text(f"Rendering in Chromium ({idx + 1}/{total_sources}): {source_url}")
            progress_bar.progress((idx + 1) / total_sources)

            try:
                # Navigate and wait for initial DOM loaded
                response = page.goto(source_url, wait_until="domcontentloaded", timeout=25000)
                time.sleep(1.5)  # Brief pause for client-side JS / React hydration

                status_code = response.status if response else "Unknown"

                # Extract resolved links directly from the live rendered browser DOM
                dom_links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a')).map(a => ({
                        href: a.href,
                        text: a.innerText ? a.innerText.trim() : '[No Anchor Text]',
                        context: a.parentElement ? a.parentElement.innerText.trim().replace(/\\s+/g, ' ') : ''
                    }));
                }""")

                source_clean = clean_url(source_url)
                matched_count = 0

                for link in dom_links:
                    raw_href = link["href"]
                    href_clean = clean_url(raw_href)

                    # Match target URL (excluding self-referencing links)
                    if href_clean in target_cleans and href_clean != source_clean:
                        matched_count += 1
                        snippet = link["context"][:160] + "..." if len(link["context"]) > 160 else link["context"]

                        inbound_db[href_clean].append({
                            "source_url": source_url,
                            "anchor_text": link["text"] or "[Image / Empty Anchor]",
                            "raw_href": raw_href,
                            "context_snippet": snippet
                        })

                audit_logs.append({
                    "Source Page": source_url,
                    "HTTP Status": status_code,
                    "DOM Links Extracted": len(dom_links),
                    "Target Matches Found": matched_count
                })

            except Exception as e:
                audit_logs.append({
                    "Source Page": source_url,
                    "HTTP Status": f"Error: {type(e).__name__}",
                    "DOM Links Extracted": 0,
                    "Target Matches Found": 0
                })

        browser.close()

    status_text.empty()
    progress_bar.empty()

    # Format output results
    results = []
    for clean_u, original_u in target_map.items():
        refs = inbound_db[clean_u]
        results.append({
            "target_url": original_u,
            "is_orphan": "Yes" if len(refs) == 0 else "No",
            "inbound_count": len(refs),
            "references": refs
        })

    return results, audit_logs


# --- Streamlit UI Config & Layout ---
st.set_page_config(page_title="Orphan Page Checker", layout="wide")
st.title("🌐 Real-Browser (Playwright) Orphan Checker")
st.caption("Renders full JavaScript DOM in Chromium to reliably detect dynamic, client-side, and React-rendered internal links.")

col1, col2 = st.columns(2)

with col1:
    user_targets = st.text_area(
        "Target URLs to Check (One per line):",
        height=180,
        value="https://www.upfluence.com/platform/feature-find-influencers"
    )

with col2:
    user_sources = st.text_area(
        "Source Pages to Scan (One per line):",
        height=180,
        value="https://www.upfluence.com/influencer-marketing/how-to-find-the-perfect-influencers-for-your-niche"
    )

if st.button("Run Orphan Check", type="primary"):
    targets = [t.strip() for t in user_targets.splitlines() if t.strip()]
    sources = [s.strip() for s in user_sources.splitlines() if s.strip()]

    if not targets or not sources:
        st.error("Please provide both Target URLs and Source Pages to scan.")
    else:
        with st.spinner("Running Chromium browser scan..."):
            results, audit_logs = crawl_with_playwright(targets, sources)

        # Overview Metrics
        orphans = sum(1 for r in results if r["is_orphan"] == "Yes")
        m1, m2, m3 = st.columns(3)
        m1.metric("Target URLs Checked", len(results))
        m2.metric("Orphan Pages Detected", orphans)
        m3.metric("Linked Pages Found", len(results) - orphans)

        st.markdown("---")
        st.subheader("Results Overview")

        df = pd.DataFrame([
            {
                "Target URL": r["target_url"],
                "Is Orphan?": r["is_orphan"],
                "Inbound Links Found": r["inbound_count"]
            }
            for r in results
        ])
        st.dataframe(df, use_container_width=True)

        st.markdown("### Inbound Link Evidence")
        for item in results:
            if item["is_orphan"] == "No":
                with st.expander(f"🟢 LINKED ({item['inbound_count']} links found) — {item['target_url']}"):
                    for ref in item["references"]:
                        st.markdown(f"**Source Page:** [{ref['source_url']}]({ref['source_url']})")
                        st.markdown(f"- **Anchor Text:** `{ref['anchor_text']}`")
                        st.markdown(f"- **Full Target Href:** `{ref['raw_href']}`")
                        st.markdown(f"- **Surrounding DOM Text:** *\"{ref['context_snippet']}\"*")
                        st.divider()
            else:
                with st.expander(f"🔴 ORPHAN — {item['target_url']}"):
                    st.warning("No incoming links detected in the rendered browser DOM across the scanned source pages.")

        st.markdown("---")
        with st.expander("🔍 Browser Crawl Diagnostic Log"):
            st.dataframe(pd.DataFrame(audit_logs), use_container_width=True)
