"""Set the reserved arXiv link after a real identifier is assigned."""
import argparse, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('identifier',help='e.g. a real YYMM.NNNNN identifier or https://arxiv.org/abs/... URL')
a=p.parse_args();identifier=a.identifier.removeprefix('https://arxiv.org/abs/')
if not re.fullmatch(r'\d{4}\.\d{4,5}(?:v\d+)?',identifier): p.error('Expected an assigned modern arXiv identifier')
url='https://arxiv.org/abs/'+identifier
page=ROOT/'index.html';html=page.read_text(encoding='utf-8')
html,n=re.subn(r'<a\b[^>]*\bid="arxiv-link"[^>]*>.*?</a>',
    '<a class="btn" id="arxiv-link" href="'+url+'" target="_blank" rel="noopener noreferrer">arXiv</a>',html,flags=re.S)
if n!=1: raise RuntimeError('Expected exactly one reserved arXiv link')
html=html.replace('arXiv link forthcoming','arXiv preprint')
page.write_text(html,encoding='utf-8')
print('Updated homepage arXiv button:',url)
print('Review the identifier, update citation metadata if needed, then commit and push.')
