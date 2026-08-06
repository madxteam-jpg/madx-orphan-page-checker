import streamlit as st
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse
import pandas as pd
import time
import subprocess
import sys

# Auto-install Playwright chromium binaries on cloud boot
try:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
except Exception as e:
    print(f"Playwright installation warning: {e}")

def clean_url(url):
    """Normalizes URLs by stripping protocol, www, trailing slashes, and parameters."""
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
    Launches a real Chromium browser to load pages, execute client-side JS,
    and extract fully resolved links directly from the browser DOM.
    """
    target_map = {clean_url(u): u.strip() for u in target_urls if u.strip()}
    target_cleans = set(target_map.keys())

    inbound_db = {clean: [] for clean in target_cleans}
    audit_logs = []

    progress_bar = st.progress(0)
    status_text = st.empty()
    total_sources = len(source_urls)

    with sync_playwright() as p:
        # Launch real headless browser with desktop user-agent
        browser = p.chromium.launch(headless=True)
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
                # Load page and wait for DOM network idle / JS execution
                response = page.goto(source_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(1.5)  # Allow client-side JS/hydration to run

                status_code = response.status if response else "Unknown"

                # Extract resolved links directly from the live browser DOM
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

                    # Match target URL (excluding self-links)
                    if href_clean in target_cleans and href_clean != source_clean:
                        matched_count += 1
                        snippet = link["context"][:160] + "..." if len(link["context"]) > 160 else link["context"]

                        inbound_db[href_clean].append({
                            "source_url": source_url,
                            "anchor_text": link["text"] or "[Image / Empty]",
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

    # Format output
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

# --- Streamlit UI ---
st.set_page_config(page_title="Playwright Orphan Checker", layout="wide")
st.title("🌐 Real-Browser (Playwright) Orphan Checker")
st.caption("Renders full JavaScript DOM to detect dynamically loaded and client-side rendered links.")

col1, col2 = st.columns(2)

with col1:
    user_targets = st.text_area(
        "Target URLs to Check (One per line):",
        height=160,
        value="https://www.upfluence.com/platform/feature-find-influencers"
    )

with col2:
    user_sources = st.text_area(
        "Source Pages to Crawl (One per line):",
        height=160,
        value="https://www.upfluence.com/influencer-marketing/how-to-find-the-perfect-influencers-for-your-niche"
    )

if st.button("Run Browser Check", type="primary"):
    targets = [t.strip() for t in user_targets.splitlines() if t.strip()]
    sources = [s.strip() for s in user_sources.splitlines() if s.strip()]

    if not targets or not sources:
        st.error("Please provide both Target URLs and Source Pages to scan.")
    else:
        results, audit_logs = crawl_with_playwright(targets, sources)

        # Metrics Overview
        orphans = sum(1 for r in results if r["is_orphan"] == "Yes")
        m1, m2, m3 = st.columns(3)
        m1.metric("Target URLs Checked", len(results))
        m2.metric("Orphan Pages", orphans)
        m3.metric("Linked Pages", len(results) - orphans)

        st.markdown("---")
        st.subheader("Results Table")

        df = pd.DataFrame([
            {"Target URL": r["target_url"], "Is Orphan?": r["is_orphan"], "Inbound Links Found": r["inbound_count"]}
            for r in results
        ])
        st.dataframe(df, use_container_width=True)

        st.markdown("### Verified Inbound Link Evidence")
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
                    st.warning("No incoming links detected in the rendered browser DOM across the scanned pages.")

        st.markdown("---")
        with st.expander("🔍 Browser Crawl Audit Log"):
            st.dataframe(pd.DataFrame(audit_logs), use_container_width=True)
