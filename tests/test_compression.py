"""Run from project root: python -m unittest discover -s tests. Playwright is optional."""
import unittest, os, json
from portal_browser import launch_browser
from portal_compression import compress, encode, decoder, wrapper, optimize, ENCODINGS
class Tests(unittest.TestCase):
    def test_encoding_safety(self):
        for n in range(1,100):
            for enc in ENCODINGS:
                self.assertNotIn('<',encode(os.urandom(n),enc))
    def test_auto_never_expands(self):
        text='<p>hello</p>'
        result,selected,rows,_=optimize(text,'test',profile='compatible',iters=1)
        self.assertLessEqual(len(result.encode()),len(text.encode()))
    def test_browser_combinations(self):
        try: from playwright.sync_api import sync_playwright
        except ImportError: self.skipTest('Installare playwright per i test browser')
        raw='<!doctype html><html><body><div id="ok">àè 🐣 &lt;/script&gt;</div></body></html>'
        with sync_playwright() as p:
            try: browser,_=launch_browser(p.chromium, os.environ.get('MD_PORTAL_CHROMIUM'))
            except Exception as e: self.skipTest(f'Chromium non disponibile: {e}')
            ctx=browser.new_context(offline=True)
            try:
                for a in ['gzip','deflate','zopfli','brotli','lzma']:
                    data,_=compress(raw.encode(),a,2);src,dec=decoder(a,None)
                    for enc in ENCODINGS:
                        with self.subTest(algorithm=a,encoding=enc):
                            page=ctx.new_page()
                            try:
                                page.set_content(wrapper(encode(data,enc),a,enc,'Test',len(raw.encode()),src,dec))
                                page.locator('#ok').wait_for(timeout=5000)
                                self.assertEqual(page.locator('#ok').inner_text(),'àè 🐣 </script>')
                                self.assertEqual(page.locator('#loading').count(),0)
                            finally: page.close()
            finally: browser.close()
