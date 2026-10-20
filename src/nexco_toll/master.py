"""公式JSをコードとして実行せず、配列リテラルだけ読み取る。"""
import ast
import re
import unicodedata
from .models import IC, ParseError, ICNotFoundError, AmbiguousICError


def normalize(value):
    value = unicodedata.normalize('NFKC', value).strip()
    return re.sub(r'(?:インターチェンジ|出入口|入口|出口|IC)$', '', value, flags=re.I)


def _array(text, name):
    match = re.search(r'\bvar\s+' + re.escape(name) + r'\s*=\s*(\[)', text)
    if not match: raise ParseError('IC原本の配列がありません')
    start = match.start(1)
    level = 0
    quote = None
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escape: escape = False
            elif char == '\\': escape = True
            elif char == quote: quote = None
        elif char in "'\"": quote = char
        elif char == '[': level += 1
        elif char == ']':
            level -= 1
            if level == 0:
                try: return ast.literal_eval(text[start:index+1])
                except (ValueError, SyntaxError, RecursionError): break
    raise ParseError('IC原本の配列形式が変わりました')


class ICMaster:
    def __init__(self, text):
        try:
            if len(text) > 2_000_000: raise ValueError()
            version = re.search(r"\bvar\s+hiver\s*=\s*['\"](v[0-9]+)['\"]", text)
            if not version: raise ValueError()
            roads, prefectures = _array(text,'rdM'), _array(text,'preM')
            prefs = {str(row[0]): str(row[1]) for row in prefectures}
            rows = _array(text,'icM')
            if not 100 < len(rows) < 20000: raise ValueError()
            self.ics=[]
            for row in rows:
                if not isinstance(row,list) or len(row)<7 or not all(isinstance(v,str) for v in row[:7]): raise ValueError()
                if not re.fullmatch(r'[0-9]{7}', row[2]): raise ValueError()
                road_index=int(row[4])
                if not 0 <= road_index < len(roads): raise ValueError()
                self.ics.append(IC(row[0],row[2],prefs[row[3]],roads[road_index][1],row[1],row[5]))
            if len({ic.code for ic in self.ics}) != len(self.ics): raise ValueError()
            self.version=version[1]
        except (ValueError,TypeError,KeyError,IndexError,RecursionError):
            raise ParseError('IC原本の形式・参照関係が不正です') from None

    def candidates(self, query):
        key=normalize(query)
        if not key: return []
        exact=[ic for ic in self.ics if key in (normalize(ic.name),ic.code,normalize(ic.reading))]
        return exact or [ic for ic in self.ics if key in normalize(ic.name) or key in normalize(ic.reading)]

    def resolve(self, query):
        key=normalize(query)
        exact=[ic for ic in self.ics if key in (normalize(ic.name),ic.code)]
        if not exact: raise ICNotFoundError('IC名または7桁コードが見つかりません')
        if len(exact)!=1: raise AmbiguousICError(exact)
        return exact[0]
