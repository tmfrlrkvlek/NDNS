#_*_ coding:utf-8 _*_

from django.shortcuts import render
from searchEngine.settings import *
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import networkx, re, emoji, json
from konlpy.tag import Komoran

class RawSentenceReader:
    def __init__(self, text):
        self.text = text
        self.rgxSplitter = re.compile('([.!?:](?:["\']|(?![0-9])))')

    def __iter__(self):
        line = self.text
        ch = self.rgxSplitter.split(line)
        for s in map(lambda a, b: a + b, ch[::2], ch[1::2]):
            if not s: continue
            yield s

class TextRank:
    def __init__(self, **kargs):
        self.graph = None
        self.window = kargs.get('window', 5)
        self.coef = kargs.get('coef', 1.0)
        self.threshold = kargs.get('threshold', 0.005)
        self.dictCount = {}
        self.dictBiCount = {}
        self.dictNear = {}
        self.nTotal = 0

    def load(self, sentenceIter, wordFilter=None):
        def insertPair(a, b):
            if a > b:
                a, b = b, a
            elif a == b:
                return
            self.dictBiCount[a, b] = self.dictBiCount.get((a, b), 0) + 1

        def insertNearPair(a, b):
            self.dictNear[a, b] = self.dictNear.get((a, b), 0) + 1

        for sent in sentenceIter:
            for i, word in enumerate(sent):
                if wordFilter and not wordFilter(word): continue
                self.dictCount[word] = self.dictCount.get(word, 0) + 1
                self.nTotal += 1
                if i - 1 >= 0 and (not wordFilter or wordFilter(sent[i - 1])): insertNearPair(sent[i - 1], word)
                if i + 1 < len(sent) and (not wordFilter or wordFilter(sent[i + 1])): insertNearPair(word, sent[i + 1])
                for j in range(i + 1, min(i + self.window + 1, len(sent))):
                    if wordFilter and not wordFilter(sent[j]): continue
                    if sent[j] != word: insertPair(word, sent[j])

    def loadSents(self, sentenceIter, tokenizer=None):
        import math
        def similarity(a, b):
            n = len(a.intersection(b))
            return n / float(len(a) + len(b) - n) / (math.log(len(a) + 1) * math.log(len(b) + 1))

        if not tokenizer: rgxSplitter = re.compile('[\\s.,:;-?!()"\']+')
        sentSet = []
        for sent in filter(None, sentenceIter):
            if type(sent) == str:
                if tokenizer:
                    s = set(filter(None, tokenizer(sent)))
                else:
                    s = set(filter(None, rgxSplitter.split(sent)))
            else:
                s = set(sent)
            if len(s) < 2: continue
            self.dictCount[len(self.dictCount)] = sent
            sentSet.append(s)

        for i in range(len(self.dictCount)):
            for j in range(i + 1, len(self.dictCount)):
                s = similarity(sentSet[i], sentSet[j])
                if s < self.threshold: continue
                self.dictBiCount[i, j] = s

    def getPMI(self, a, b):
        import math
        co = self.dictNear.get((a, b), 0)
        if not co: return None
        return math.log(float(co) * self.nTotal / self.dictCount[a] / self.dictCount[b])

    def getI(self, a):
        import math
        if a not in self.dictCount: return None
        return math.log(self.nTotal / self.dictCount[a])

    def build(self):
        self.graph = networkx.Graph()
        self.graph.add_nodes_from(self.dictCount.keys())
        for (a, b), n in self.dictBiCount.items():
            self.graph.add_edge(a, b, weight=n * self.coef + (1 - self.coef))

    def rank(self):
        return networkx.pagerank(self.graph, weight='weight')

    def extract(self, ratio=0.1):
        ranks = self.rank()
        cand = sorted(ranks, key=ranks.get, reverse=True)[:int(len(ranks) * ratio)]
        pairness = {}
        startOf = {}
        tuples = {}
        for k in cand:
            tuples[(k,)] = self.getI(k) * ranks[k]
            for l in cand:
                if k == l: continue
                pmi = self.getPMI(k, l)
                if pmi: pairness[k, l] = pmi

        for (k, l) in sorted(pairness, key=pairness.get, reverse=True):
            print(k[0], l[0], pairness[k, l])
            if k not in startOf: startOf[k] = (k, l)

        for (k, l), v in pairness.items():
            pmis = v
            rs = ranks[k] * ranks[l]
            path = (k, l)
            tuples[path] = pmis / (len(path) - 1) * rs ** (1 / len(path)) * len(path)
            last = l
            while last in startOf and len(path) < 7:
                if last in path: break
                pmis += pairness[startOf[last]]
                last = startOf[last][1]
                rs *= ranks[last]
                path += (last,)
                tuples[path] = pmis / (len(path) - 1) * rs ** (1 / len(path)) * len(path)

        used = set()
        both = {}
        for k in sorted(tuples, key=tuples.get, reverse=True):
            if used.intersection(set(k)): continue
            both[k] = tuples[k]
            for w in k: used.add(w)
        return both

    def summarize(self, ratio=0.333):
        r = self.rank()
        ks = sorted(r, key=r.get, reverse=True)[int(len(r)*ratio) : int(len(r)*ratio+3 )]
        return '\n'.join(map(lambda k: self.dictCount[k][:50], sorted(ks)))


@csrf_exempt
def runtr(request):
    data = json.loads(request.body)
    tagger = Komoran()
    result = []
    for i in data:
        try:
            text = emoji.get_emoji_regexp().sub("", i['body'])
            tr = TextRank()
            stopword = set([('있', 'VV'), ('하', 'VV'), ('되', 'VV'), ('는', 'VV'), ('을','VV'), ('를', 'VV') ])
            tr.loadSents(RawSentenceReader(text), lambda sent: filter(lambda x: x not in stopword and x[1] in ('NNG', 'NNP', 'VV', 'VA'), tagger.pos(sent)))
            tr.build()
            result.append({'id': i['id'], 'body': tr.summarize(0.1)})
        except:
            result.append({'id': i['id'], 'body': "텍스트랭크가 원활하게 동작하지 못했습니다"})
            print(i['body'])
    return JsonResponse(result, safe=False)

def index(request):
    return render(request, "index.html", {'STATIC_URL': STATIC_URL})

def search(request, searchVal):
    return render(request, "search.html", {'STATIC_URL': STATIC_URL, 'searchVal':searchVal})