import unittest
from markdown_it import MarkdownIt
from bs4 import BeautifulSoup
from portal_math import math_plugin

class MathTests(unittest.TestCase):
    def test_math_tokens(self):
        src = r'''Inline $x_{ref}$ and \(\theta^2\).

$$ \Delta\theta = \theta_{ref} - \theta_{native} $$

$$
\begin{bmatrix}E_{ref}\\N_{ref}\end{bmatrix}
$$

\[ \frac{a}{b} \]

`$code$` and \$100.

```text
$$ code $$
```
'''
        env={}; soup=BeautifulSoup(MarkdownIt('commonmark').use(math_plugin).render(src,env),'html.parser')
        self.assertEqual(env['math_count'],5)
        self.assertEqual(soup.select('.math-inline')[0].text,'x_{ref}')
        self.assertIn(r'\Delta\theta',soup.select('.math-display')[0].text)
        self.assertIn(r'\\N_{ref}',soup.select('.math-display')[1].text)
        self.assertEqual(soup.select_one('code').text,'$code$')
        self.assertIn('$100',soup.get_text())
        self.assertIn('$$ code $$',soup.select_one('pre').text)
    def test_currency_and_no_math(self):
        md=MarkdownIt('commonmark').use(math_plugin);env={}
        self.assertNotIn('math-tex',md.render('Prezzi $10 e $20. `x_y`',env))
        self.assertNotIn('math_count',env)
