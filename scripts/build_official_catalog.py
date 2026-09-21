"""Extract numeric answer keys from locally downloaded official answer PDFs.

Development-only dependency: pdfplumber. Original question text/audio is linked,
not bundled. Pass a directory containing YYYY-Nx-answer.pdf files.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
norm = lambda s: unicodedata.normalize('NFKC', s or '').strip()
LABELS = {'vocabulary':'文字词汇','grammar':'语法','reading':'阅读','listening':'听力'}
# Workbook-era section timings, not a claim about current exam lengths.
TIMES = {'N1':[110,60], 'N2':[105,50], 'N3':[30,70,40], 'N4':[30,60,35], 'N5':[25,50,30]}


def paper(year, lv, folder):
    base = f'https://www.jlpt.jp/samples/sample{year}/'
    key = base+f'pdf/{lv}answer.pdf'
    out = dict(id=f'official-{year}-{lv}', title=f'{lv} 官方问题集·'+('第一集' if year==2012 else '第二集'),
        level=lv, version=str(year)+'.1', source_type='official', source='日本語能力試験公式ウェブサイト',
        source_url='https://www.jlpt.jp/e/samples/sampleindex.html', publication_year=year, exam_year=None,
        notes=f'{year}为出版年，并非考试年份。对照官方原卷填写答题卡；听力选项以原卷/原音频为准。计时采用该版问题集时期的分区时长。',
        resources=[dict(title=LABELS[s]+'原卷',url=base+f'pdf/{lv}{suffix}.pdf',kind=s) for s,suffix in [('vocabulary','V'),('grammar','G'),('reading','R'),('listening','L')]]+
        [dict(title='官方答案',url=key,kind='answer'),dict(title='听力原文',url=base+f'pdf/{lv}script.pdf',kind='script')]+
        [dict(title=f'听力問題{i}音频',url=(f'https://www.jlpt.jp/samples/sample2017/' if year==2012 and i==2 else base)+f'mp3/{lv}Q{i}.mp3',kind='audio') for i in range(1,5 if lv in ('N4','N5') else 6)],
        sections=[], questions=[])
    names = ['语言知识・阅读','听力'] if lv in ('N1','N2') else ['文字词汇','语法・阅读','听力']
    for i,(name,mins) in enumerate(zip(names,TIMES[lv])):out['sections'].append(dict(id=f's{i+1}',title=name,seconds=mins*60))
    section = 0; group = 0
    with pdfplumber.open(folder/f'{year}-{lv}-answer.pdf') as doc:
        for pno,page in enumerate(doc.pages,1):
            lines = page.extract_text_lines()
            tables = page.find_tables()
            for table in tables:
                headings=[x for x in lines if x['top']<table.bbox[1] and '●' in x['text']]
                if headings:
                    heading=headings[-1]['text']
                    section=len(names)-1 if '聴解' in heading else (1 if '文法' in heading and len(names)==3 else 0)
                near=[x for x in lines if abs(x['top']-table.bbox[1])<20 and re.search(r'問題\s*\d+',norm(x['text']))]
                if not near:raise ValueError(f'No group: {year} {lv} page {pno}')
                group=int(re.search(r'問題\s*(\d+)',norm(near[0]['text']))[1])
                rows=[[norm(c) for c in row] for row in table.extract()]
                if len(rows)==3 and section==len(names)-1 and group==5:
                    pairs=list(zip(['1','2','3(1)','3(2)'],rows[-1]))
                else:
                    if len(rows)%2:raise ValueError((year,lv,pno,rows))
                    pairs=[]
                    for i in range(0,len(rows),2):
                        pairs += [(n,a) for n,a in zip(rows[i],rows[i+1]) if n and n!='例']
                if section==len(names)-1:skill='listening'
                elif section==0 and len(names)==3:skill='vocabulary'
                elif len(names)==3:skill='grammar' if group<=3 else 'reading'
                else:
                    v_end,g_end=(4,7) if lv=='N1' else (6,9)
                    skill='vocabulary' if group<=v_end else 'grammar' if group<=g_end else 'reading'
                for n,a in pairs:
                    if not re.fullmatch(r'\d+(?:\([12]\))?',n) or a not in '1234' or len(a)!=1:raise ValueError((year,lv,n,a))
                    locator=f'{names[section]} · 問題{group} · {n}'
                    qid=f's{section+1}-g{group}-q{n}'
                    optcount=3 if skill=='listening' and group in ({'N1':[4],'N2':[4],'N3':[4,5],'N4':[3,4],'N5':[3,4]}[lv]) else 4
                    out['questions'].append(dict(id=qid,section=f's{section+1}',skill=skill,prompt=locator,
                        options=[str(i) for i in range(1,optcount+1)],answer=int(a)-1,
                        explanation='答案依据官方正答表。原题与听力请对照官方资料；本题未附原创解析。',
                        locator=locator,group=f'問題{group}',passage='',audio_text='',grammar_ids=[],
                        answer_source=dict(url=key,page=pno)))
    assert len({q['id'] for q in out['questions']})==len(out['questions'])
    return out


if __name__=='__main__':
    folder=Path(sys.argv[1]);papers=[paper(y,f'N{n}',folder) for y in (2012,2018) for n in range(5,0,-1)]
    (ROOT/'data/study/papers.json').write_text(json.dumps(papers,ensure_ascii=False,indent=2)+'\n')
    for p in papers:print(p['id'],len(p['questions']),{s:sum(q['skill']==s for q in p['questions']) for s in LABELS})
