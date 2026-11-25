import unittest
from portal_nav import build_navigation,navigation_order

def p(path,title=None,**meta):return {'path':path,'title':title or path,'meta':meta}
class NavigationTests(unittest.TestCase):
    def test_coverage_and_groups(self):
        pages=[p('README.md'),p('doc/stack.md'),p('doc/risorse/README.md'),p('doc/risorse/a.md'),p('apps/afc-ticketing/README.md'),p('apps/afc-ticketing/doc/README.md'),p('apps/afc-ticketing/doc/features/F10.md',id='F10',type='feature'),p('apps/afc-ticketing/doc/features/F2.md',id='F2',type='feature'),p('apps/afc-ticketing/doc/ROADMAP.md'),p('apps/core-profilemanager/doc/technical/db.md'),p('deploy/dev/README.md'),p('_packages/a/README.md'),p('other/README.md'),p('other/x.md')]
        n=build_navigation(pages); o=navigation_order(n)
        self.assertCountEqual(o,[x['path'] for x in pages]); self.assertEqual([x['label'] for x in n],['Main','Ecosistema','Servizi','Deploy','Packages','Other'])
        self.assertLess(o.index(pages[7]['path']),o.index(pages[6]['path']));self.assertLess(o.index(pages[8]['path']),o.index(pages[7]['path']))
    def test_readme_clickable(self):
        n=build_navigation([p('docs/sub/README.md'),p('docs/sub/a.md')]);b=n[0]['children'][0];self.assertEqual(b['page'],'docs/sub/README.md');self.assertEqual(b['children'][0]['page'],'docs/sub/a.md')
