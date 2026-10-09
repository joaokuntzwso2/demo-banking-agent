"""Optional real-browser sanity test with local Docker services.

Install: pip install playwright && python -m playwright install chromium
Run: AURORA_RUN_BROWSER_TESTS=1 AURORA_PORTAL_URL=http://localhost:8300 pytest -q tests/e2e/
No automatic bank mutation. Reads and chat only.
"""
import os
import pytest
if os.getenv('AURORA_RUN_BROWSER_TESTS', '') != '1':
    pytest.skip('Opt-in browser test: set AURORA_RUN_BROWSER_TESTS=1 and install Chromium', allow_module_level=True)
pytest.importorskip('playwright')
from playwright.sync_api import sync_playwright,expect

def test_english_spanish_ui_and_login():
    url=os.getenv('AURORA_PORTAL_URL','http://localhost:8300')
    code=os.getenv('DEMO_ACCESS_CODE','aurora-local-demo-only')
    with sync_playwright() as browser:
        p=browser.chromium.launch(headless=True)
        page=p.new_page(viewport={'width':1440,'height':900})
        page.goto(url)
        expect(page.locator('#loginOverlay')).to_be_visible()
        page.locator('#loginCode').fill(code)
        page.locator('#loginForm button').click()
        expect(page.locator('#loginOverlay')).to_be_hidden(timeout=10000)
        expect(page.locator('#balance')).to_contain_text('R$',timeout=10000)
        page.locator('#lang').select_option('es')
        expect(page.locator('[data-i="assistant"]')).to_contain_text('Aurora')
        page.locator('#ask').click()
        page.locator('#entry').fill('¿Cuánto dinero me queda después de mis próximos gastos?')
        page.locator('#chatForm button[type="submit"]').click()
        expect(page.locator('#messages .bot').last).to_contain_text('R$',timeout=45000)
        page.locator('#close').click()
        page.locator('[data-tab="cards"]').click()
        expect(page.locator('.bank-card-detail').first).to_be_visible()
        p.close()
