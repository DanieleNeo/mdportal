import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from md_portal import explicit_anchor_plugin, register_explicit_anchors

class ExplicitAnchorTests(unittest.TestCase):
    def parse(self, source):
        md=MarkdownIt('commonmark', {'html':False}).enable('table').use(explicit_anchor_plugin)
        soup=BeautifulSoup(md.render(source),'html.parser')
        aliases={}; warnings=[]
        count=register_explicit_anchors(soup,aliases,set(),warnings.append)
        return soup,aliases,warnings,count

    def test_standalone_table_and_named(self):
        soup,aliases,warnings,count=self.parse('''<a id="cap-1"></a>

# Capitolo

| Ref |
| --- |
| <a id="ref-a1"></a>**A1** |

<a name='ref-d1'></a> Fonte
''')
        self.assertEqual(count,3)
        self.assertEqual(warnings,[])
        self.assertEqual(aliases,{'cap-1':'cap-1','ref-a1':'ref-a1','ref-d1':'ref-d1'})
        self.assertEqual(soup.select_one('#section-cap-1').parent.name,'[document]')
        self.assertEqual(soup.select_one('#section-ref-a1').parent.name,'td')

    def test_code_and_html_not_enabled(self):
        soup,aliases,warnings,count=self.parse('''`<a id="inline"></a>`

```html
<a id="fenced"></a>
```

\\<a id="escaped"></a>

<a id="unsafe" onclick="alert(1)"></a>

<script>alert(1)</script>

<img src=x onerror=alert(1)>
''')
        self.assertEqual(count,0)
        self.assertEqual(aliases,{})
        self.assertIsNone(soup.find('script'))
        self.assertIsNone(soup.find('img'))
        self.assertIn('<a id="fenced"></a>',soup.pre.text)
        self.assertIn('<a id="escaped"></a>',soup.get_text())

    def test_duplicate_and_heading_collision(self):
        md=MarkdownIt('commonmark',{'html':False}).use(explicit_anchor_plugin)
        soup=BeautifulSoup(md.render('# Ref\n\n<a id="ref"></a>\n\n<a id="ref"></a>'),'html.parser')
        soup.h1['id']='section-ref'
        aliases={'ref':'ref'}; warnings=[]
        register_explicit_anchors(soup,aliases,{'ref'},warnings.append)
        self.assertEqual(aliases['ref'],'anchor-ref-1')
        self.assertEqual(len(soup.select('#section-ref')),1)
        self.assertEqual(len(soup.select('#section-anchor-ref-1')),1)
        self.assertEqual(warnings,['Duplicate explicit anchor: #ref'])

    def test_cross_document_and_missing_target_strict(self):
        script=Path(__file__).resolve().parent.parent/'md_portal.py'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); out=root/'output.html'
            (root/'a.md').write_text('# A\n\n[fonte](b.md#Ref%3A1)\n',encoding='utf-8')
            (root/'b.md').write_text('# B\n\n<a id="Ref:1"></a>\n\nTest\n',encoding='utf-8')
            cmd=[sys.executable,str(script),str(root),'--output',str(out),'--strict','--compression','none','--no-math']
            r=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            soup=BeautifulSoup(out.read_text(encoding='utf-8'),'html.parser')
            self.assertIsNotNone(soup.find('a',href='#/b.md#Ref%3A1'))
            self.assertIsNotNone(soup.select_one('[id="section-Ref:1"]'))
            (root/'a.md').write_text('# A\n\n[fonte](b.md#missing)\n',encoding='utf-8')
            r=subprocess.run(cmd,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('Unresolved anchor: b.md#missing',r.stderr)
