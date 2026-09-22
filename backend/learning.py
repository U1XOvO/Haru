"""Inspectable reading aids and passive exposure, separate from SM-2 reviews."""
import json
import re
import unicodedata
from datetime import datetime

from curriculum import CARDS
from llm import AppError

TOKEN = dict(surface='原文连续片段，含标点和空格', reading='该片段的假名读音；标点为空',
             lemma='单词辞书形；标点为空', meaning='在本句中的中文义；标点为空')
PITCH_SOURCE = 'https://www.jpf.go.jp/j/project/japanese/teach/tsushin/research/202503.html'
# Small, explicitly sourced teaching examples; never accept an LLM pitch as verified data.
PITCH = {('雨', 'あめ'): 1, ('飴', 'あめ'): 0}


def moras(reading):
    reading = unicodedata.normalize('NFKC', reading)
    if not re.fullmatch(r'[ぁ-ゖァ-ヺー]+', reading):
        return []
    out = []
    for ch in reading:
        if ch in 'ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ' and out:
            out[-1] += ch
        else:
            out.append(ch)
    return out


def pronunciation(word, reading):
    units = moras(reading)
    nucleus = PITCH.get((word, reading))
    pattern = [] if nucleus is None else [
        '高' if (i == 0 if nucleus == 1 else i > 0 and (nucleus == 0 or i < nucleus)) else '低'
        for i in range(len(units))]
    return dict(moras=units, count=len(units) if units else None, nucleus=nucleus,
                pattern=pattern, source=PITCH_SOURCE if nucleus is not None else None,
                source_label='国際交流基金 · 日本語教育通信（2025年3月）' if nucleus is not None else '',
                checked='2026-09-20' if nucleus is not None else None)


class Learning:
    def init_learning(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS annotations(sentence TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS encounters(word TEXT NOT NULL,ref TEXT NOT NULL,kind TEXT NOT NULL,
            created TEXT NOT NULL,PRIMARY KEY(word,ref,kind));
        CREATE TABLE IF NOT EXISTS lexical_entries(word TEXT PRIMARY KEY,data TEXT NOT NULL);
        ''')

    def reading_lookup(self, p):
        sentences = p.get('sentences')
        if not isinstance(sentences, list) or not 1 <= len(sentences) <= 16 or any(
                not isinstance(s, str) or not 0 < len(s) <= 1200 for s in sentences):
            raise AppError('每次可读取1–16句已保存注音。')
        marks = ','.join('?' for _ in sentences)
        items = []; checked = []
        size = 0
        rows = {r['sentence']:r['data'] for r in self.db.execute(f'SELECT sentence,data FROM annotations WHERE sentence IN ({marks})', sentences)}
        for sentence in dict.fromkeys(sentences):
            raw = rows.get(sentence)
            length = len(raw.encode('utf-8')) if raw else 0
            if length <= 1_500_000 and size + length > 1_500_000:
                break
            # Oversized legacy annotations cannot be sent through bounded IPC.
            checked.append(sentence)
            if raw and length <= 1_500_000:
                size += length
                items.append(json.loads(raw))
        return dict(items=items,checked=checked)

    def annotate(self, p):
        sentence = p.get('text')
        if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > 1200:
            raise AppError('注音文本应为1–1200字。')
        row = self.db.execute('SELECT data FROM annotations WHERE sentence=?', (sentence,)).fetchone()
        if row:
            return json.loads(row[0])
        d = self.ai('逐词标注日语。tokens 的 surface 按原顺序拼接必须与 sentence 完全一致，保留空格和标点。'
                    '动词按整段活用词标注读音并给出辞书形，不将中文汉字读音套入日语。'
                    '助词和助动词单列。不得虚构声调；无法确定的读音和词义用空字符串。',
                    {'sentence': sentence}, {'tokens': [TOKEN]})
        tokens = d.get('tokens')
        if not isinstance(tokens, list) or not 1 <= len(tokens) <= 300:
            raise AppError('逐词注音结构无效，未保存。')
        for t in tokens:
            if not isinstance(t, dict) or any(not isinstance(t.get(k), str) or len(t[k]) > 1200 for k in TOKEN):
                raise AppError('逐词注音字段无效，未保存。')
            if not t['surface'] or (t['reading'] and not moras(t['reading'])):
                raise AppError('逐词读音无效，未保存。')
            if len(t['lemma']) > 100 or len(t['reading']) > 200:
                raise AppError('词条过长，未保存。')
        if ''.join(t['surface'] for t in tokens) != sentence:
            raise AppError('注音与原文不一致，未保存，请重试。')
        result = dict(sentence=sentence, tokens=[{k:t[k] for k in TOKEN} for t in tokens],
                      source=d['source'], model=d['model'])
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO annotations VALUES (?,?)', (sentence, json.dumps(result, ensure_ascii=False)))
        return json.loads(self.db.execute('SELECT data FROM annotations WHERE sentence=?', (sentence,)).fetchone()[0])

    def dictionary(self, p):
        word = p.get('word'); sentence = p.get('sentence', '')
        if not isinstance(word, str) or not word.strip() or len(word) > 100 or not isinstance(sentence, str) or len(sentence) > 1200:
            raise AppError('请输入不超过100字的日语词语。')
        word = word.strip()
        # A validated contextual token supplies the lemma; arbitrary suffix stripping is not safe.
        token = None
        row = self.db.execute('SELECT data FROM annotations WHERE sentence=?', (sentence,)).fetchone()
        if row:
            token = next((t for t in json.loads(row[0])['tokens'] if t['surface'] == word and t['lemma']), None)
        lemma = token['lemma'] if token else word
        row = self.db.execute('SELECT data FROM cards WHERE word=?', (lemma,)).fetchone()
        cached = self.db.execute('SELECT data FROM lexical_entries WHERE word=?', (lemma,)).fetchone()
        seed = next((c for c in CARDS if c['word'] == lemma), None)
        if row:
            d = json.loads(row[0])
        elif cached:
            d = json.loads(cached[0])
        elif seed:
            d = dict(seed, source='内置原创', model=None)
        else:
            from service import CARD, fields, text
            d = self.ai('查询这个日语词在原句中的用法，word 必须为对应日语辞书形。'
                        '给出完整可保存词卡，不要把整句当词条，不给声调。',
                        {'word': word, 'lemma_hint': lemma, 'sentence': sentence}, CARD)
            fields(d, CARD); text(d['word'], '词条', 100)
            if not moras(d['reading']): raise AppError('词典读音不是有效假名，未保存。')
        result = dict(d, queried=word, context_meaning=token['meaning'] if token else '',
                      pronunciation=pronunciation(d['word'], d['reading']))
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO lexical_entries VALUES (?,?)', (d['word'], json.dumps(d, ensure_ascii=False)))
            self.db.execute('INSERT OR IGNORE INTO encounters VALUES (?,?,?,?)',
                            (d['word'], sentence or word, 'lookup', datetime.now().isoformat(timespec='seconds')))
        return result

    def dictionary_add(self, p):
        row = self.db.execute('SELECT data FROM lexical_entries WHERE word=?', (p.get('word'),)).fetchone()
        if not row: raise AppError('请先打开词典查询此词。')
        return self.add_card(json.loads(row[0]), compact=p.get('compact') is True)

    def encounter(self, p):
        """Only persisted teaching text is eligible; repeat renders are idempotent."""
        return self.encounters({'refs': [p.get('ref')]})

    def encounters(self, p):
        refs = p.get('refs')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 40 or any(
                not isinstance(ref, str) or len(ref) > 100 for ref in refs):
            raise AppError('每次可记录1–40个学习来源。')
        records = [(word, ref, 'exposure', datetime.now().isoformat(timespec='seconds'))
                   for ref in dict.fromkeys(refs) for word in self._encounter_words(ref)]
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO encounters VALUES (?,?,?,?)', records)
        return {'recorded': len(records)}

    def _encounter_words(self, ref):
        if not isinstance(ref, str): raise AppError('学习来源无效。')
        if ref.startswith('message:'):
            row = self.db.execute("SELECT data FROM messages WHERE id=? AND role='assistant'", (ref[8:],)).fetchone()
            if not row: raise AppError('对话来源不存在。')
            d = json.loads(row[0]); passages = [d.get('jp', '')]
        else:
            d = self.content(ref)
            if d['kind'] not in ('lesson', 'immersion', 'remedial'): raise AppError('此内容不记录词汇接触。')
            passages = [x['jp'] for x in d.get('examples', d.get('sentences', []))]
        tokens = []
        if passages:
            placeholders=','.join('?' for _ in passages)
            for row in self.db.execute(
                    f'SELECT data FROM annotations WHERE sentence IN ({placeholders})', passages):
                tokens += json.loads(row['data'])['tokens']
        seen = {t['lemma'] for t in tokens if t['lemma'] and t['meaning']}
        # Use known complete examples until a sentence has validated token boundaries.
        # Substring matching would incorrectly count 本 inside 日本語.
        entries = list(CARDS)
        if passages:
            placeholders=','.join('?' for _ in passages)
            entries += [json.loads(r[0]) for r in self.db.execute(
                f"SELECT data FROM cards WHERE json_extract(data,'$.example') IN ({placeholders})", passages)]
        seen.update(c['word'] for c in entries if c['example'] in passages and c['word'] in c['example'])
        return seen

    def knowledge(self, p):
        items = {}
        for r in self.db.execute('SELECT word,kind,COUNT(*) AS n FROM encounters GROUP BY word,kind'):
            d = items.setdefault(r['word'], dict(word=r['word'], exposure=0, lookup=0, reviews=0, recalled=0, last_review=None))
            d[r['kind']] = r['n']
        for r in self.db.execute("SELECT json_extract(data,'$.word') AS word,COUNT(*) AS reviews,SUM(json_extract(data,'$.quality')>=3) AS recalled,MAX(created) AS last_review FROM events WHERE kind='review' GROUP BY json_extract(data,'$.word')"):
            w = r['word']
            if not w: continue
            d = items.setdefault(w, dict(word=w, exposure=0, lookup=0, reviews=0, recalled=0, last_review=None))
            d.update(reviews=r['reviews'], recalled=r['recalled'], last_review=r['last_review'])
        for r in self.db.execute('SELECT word FROM cards'):
            items.setdefault(r[0], dict(word=r[0], exposure=0, lookup=0, reviews=0, recalled=0, last_review=None))
        result = sorted(items.values(), key=lambda d: (-d['reviews'], -d['exposure'], d['word']))
        if 'limit' in p:
            from service import integer
            return result[:integer(p['limit'],1,100,'词汇统计数量')]
        return result

    def review_targets(self):
        rows = self.db.execute('SELECT word,data,id FROM cards WHERE due<=? ORDER BY ease,due,word LIMIT 3',
                               (datetime.now().isoformat(timespec='seconds'),)).fetchall()
        targets=[]
        for r in rows:
            data=json.loads(r['data'])
            targets.append(dict(word=r['word'],reading=data['reading'],meaning=data['meaning'],
                                card_id=r['id'],reason='词卡已到期'))
        return targets

    def apply_review_targets(self, d, targets, passages):
        """Report observed surface coverage, never mark passive reuse as a review."""
        passage = '\n'.join(passages)
        d['review_targets'] = [dict(t, covered=t['word'] in passage) for t in targets]
        return d
