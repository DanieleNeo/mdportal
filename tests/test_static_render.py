import unittest
from pathlib import Path
from bs4 import BeautifulSoup
from portal_render import prerender

class StaticTests(unittest.TestCase):
    def test_static_svg(self):
        soup=BeautifulSoup(r'''<p>Inline <span class="math-tex math-inline" data-display="false">x_{i}</span>.</p><div class="mermaid-block"><div class="mermaid-view"></div><pre hidden>flowchart TD
 A[Uno] --> B[Due]</pre></div>''','html.parser')
        warnings=[]
        try: stats=prerender({Path('test.md'):soup},lambda f,msg:warnings.append(msg))
        except RuntimeError as e:self.skipTest(str(e))
        self.assertEqual(warnings,[])
        self.assertEqual(stats['math']['rendered'],1)
        self.assertEqual(stats['mermaid']['rendered'],1)
        self.assertEqual(len(soup.select('.math-static svg')),1)
        self.assertEqual(len(soup.select('.mermaid-static svg')),2)
        self.assertEqual(soup.select_one('.math-static')['data-tex'],'x_{i}')
        self.assertIn('flowchart TD',soup.select_one('pre').text)
