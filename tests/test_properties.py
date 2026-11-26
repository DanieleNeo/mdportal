import unittest
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from md_portal import read_metadata, resolve_inline_properties

class PropertiesTests(unittest.TestCase):
    def test_service_name_description(self):
        source='---\ntype: service\nname: ilm-degraded-validation\ndescription: "Servizio <ILM> & offline"\n---\n# `=this.name`\n\n`=this.description`\n'
        warnings=[]
        body,meta,properties=read_metadata(source,'service.md',warnings.append)
        soup=BeautifulSoup(MarkdownIt().render(body),'html.parser')
        self.assertEqual(resolve_inline_properties(soup,properties,warnings.append),2)
        self.assertEqual(soup.h1.text,'ilm-degraded-validation')
        self.assertEqual(soup.p.text,'Servizio <ILM> & offline')
        self.assertIn('&lt;ILM&gt; &amp;',str(soup))
        self.assertEqual(warnings,[])
    def test_fences_missing_expressions(self):
        soup=BeautifulSoup('<pre><code>=this.name</code></pre><p><code>=this.missing</code> <code>=this.name + 1</code> <code>=this.zero</code> <code>=this.flag</code> <code>=this.items</code></p>','html.parser')
        warnings=[]
        self.assertEqual(resolve_inline_properties(soup,{'name':'test','zero':0,'flag':False,'items':[1]},warnings.append),2)
        self.assertEqual(soup.pre.code.text,'=this.name')
        self.assertIn('=this.name + 1',soup.p.text)
        self.assertIn('0 false',soup.p.text)
        self.assertEqual(len(warnings),2)
