"""The comment text earlier taxjson versions wrote into ticker.map.

`taxjson format-map` (lib/ticker_map_format) regenerates the header: a
comment paragraph an earlier version wrote is template text, not the
user's note, and is replaced by the current header. Every header and
explanatory paragraph taxjson ever wrote into a ticker.map is recognised
here, by hash:

- the `taxjson init` templates (the commented `_TEMPLATE_TICKER_MAP` of
  every release before the grouped layout, and the first grouped
  layout's `## ` header and group text);
- the header of the earliest projects (written before `taxjson init` had
  a template: "symbol rules for the taxjson pipeline", "Each line is:
  KEYWORD from [to]", "'#' starts a comment");
- the one-line header `taxjson migrate` gave a ticker.map it created.

The texts themselves are in tests/fixtures/ticker_map/legacy_headers.txt
(their symbols replaced by made-up ones; a listing symbol is masked
before hashing, so the hashes are the same); test_ticker_map_legacy
recomputes these tables from it. When the header or a group's text
changes, add the old text to the fixture and the hashes it prints here.

Three tables, sha256 of the normalised text, first 16 hex digits:

- LINE_HASHES: each comment line (norm_line: leading `#`s and blanks
  dropped, blanks collapsed, years generic, a listing symbol masked).
  A run of comment lines every one of which is here is template text
  (a header split into pieces is recognised piece by piece).
- PARAGRAPH_HASHES: each paragraph (the lines between bare `#` lines)
  and each whole header, joined (norm_para: norm_line of each line,
  joined, lower case): a paragraph re-wrapped or re-cased is still
  template text, and so is one sitting in a run with the user's own
  lines (the owner of a map that carried the earliest header had
  written their notes directly below its last line).
- FINGERPRINTS: per paragraph of at least NEAR_MIN_WORDS words, the
  hash of each word (4 hex digits): a run of comments that holds at
  least NEAR_SHARE of a paragraph's words in order, and is not that
  paragraph, is a header the user edited — kept, with an Info line.
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Iterable, List, Sequence, Set, Tuple

from taxjson.lib.config_template import _hash, _norm

# A listing symbol (AEM.US, ABCX.U.TO, BRK-B.US, OLDCO.TO): masked, so
# the fixture's made-up symbols hash like the templates' own.
_SYMBOL = re.compile(r"\b[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*\.(?:US|TO|V)\b")

NEAR_SHARE = 0.9
NEAR_MIN_WORDS = 8


def norm_line(text: str) -> str:
    """A comment line compared for "is this an earlier template's line"
    (lib/config_template._norm, a listing symbol masked)."""
    return _SYMBOL.sub("SYM", _norm(text))


def norm_para(lines: Iterable[str]) -> str:
    """Comment lines compared as one paragraph: norm_line of each,
    joined, blanks collapsed, lower case."""
    return " ".join(n for n in (norm_line(ln) for ln in lines) if n).lower()


def _word(w: str) -> str:
    return hashlib.sha256(w.encode("utf-8")).hexdigest()[:4]


def words(lines: Iterable[str]) -> List[str]:
    """The word hashes of comment lines (FINGERPRINTS' form)."""
    return [_word(w) for w in norm_para(lines).split()]


def is_legacy_line(line: str) -> bool:
    n = norm_line(line.strip())
    return bool(n) and _hash(n) in LINE_HASHES


def is_legacy_paragraph(lines: Sequence[str]) -> bool:
    n = norm_para(lines)
    return bool(n) and _hash(n) in PARAGRAPH_HASHES


def near_legacy(lines: Sequence[str]) -> bool:
    """Do `lines` hold at least NEAR_SHARE of an earlier template
    paragraph's words, in order (a paragraph the user edited)?"""
    have = words(lines)
    if not have:
        return False
    have_set = set(have)
    for fp in _PRINTS:
        need = len(fp) * NEAR_SHARE
        if sum(1 for w in fp if w in have_set) < need:
            continue
        sm = SequenceMatcher(None, fp, have, autojunk=False)
        if sum(b.size for b in sm.get_matching_blocks()) >= need:
            return True
    return False


# --------------------------------------------------------------- corpus

def _is_comment(ln: str) -> bool:
    return ln.strip().startswith("#")


def _bare(ln: str) -> bool:
    return ln.strip() == "#"


def corpus_paragraphs(text: str) -> List[List[str]]:
    """The prose paragraphs of one earlier header (a fixture variant):
    its comment lines split at bare `#` lines, blank lines and group
    headings, the commented-out examples left out (after an `Examples`
    line; in the `## ` layout, every single-`#` line)."""
    lines = text.splitlines()
    layout = any(ln.startswith("## ") for ln in lines)
    out: List[List[str]] = []
    cur: List[str] = []
    examples = False

    def end() -> None:
        nonlocal cur
        if cur:
            out.append(cur)
        cur = []

    for ln in lines:
        st = ln.strip()
        if not _is_comment(ln) or _bare(ln):
            end()
            if not st:
                examples = False
            continue
        if (layout and not st.startswith("##")) or examples:
            end()
            continue
        if re.match(r"^##\s*---.*---$", st):
            end()
            continue
        cur.append(ln)
        if norm_line(st).startswith("Examples"):
            end()
            examples = True
    end()
    return out


def corpus_tables(variants: Sequence[str]
                  ) -> Tuple[Set[str], Set[str], Set[str]]:
    """(LINE_HASHES, PARAGRAPH_HASHES, FINGERPRINTS) of the earlier
    headers' texts."""
    lines: Set[str] = set()
    paras: Set[str] = set()
    prints: Set[str] = set()
    for text in variants:
        ps = corpus_paragraphs(text)
        for p in ps:
            for ln in p:
                lines.add(_hash(norm_line(ln.strip())))
            paras.add(_hash(norm_para(p)))
            w = words(p)
            if len(w) >= NEAR_MIN_WORDS:
                prints.add("".join(w))
        if len(ps) > 1:
            paras.add(_hash(norm_para([ln for p in ps for ln in p])))
    return lines, paras, prints


LINE_HASHES = frozenset("""
00097763f6d55a26 00a3f0154037172c 03d9822f67b45783 07035d8c47efb7f8
073bfe795ee08d6b 0e0f17b044b09c49 15ebd6f0aac94027 171de894c6864e48
1c463f7f571c7ae2 1e87d4fbabe97a8f 1f5f63c611e5005b 208f8b138e273883
283534607a882ad0 2b577ed893a8f4a0 2bfd6e98650a229d 2cc59cd628441c44
3008aa4f952546d6 39b12c4b6d2c6d36 3ce39165443b6351 3e27a7f2ce6a3162
418b2aaca345e818 43aa33fa691f26fe 455e9eb53cfffcf4 4863f0e62f2393a9
4c137ae81e928fba 4cb6ec017c0c31a8 4da35551ef66acda 4de745aeab6694f3
530d5b56790d7f0c 54098570a516ee12 55da8b9532665902 5766c4673142b2ab
5968bb3244cae8a9 5da5373b81e28e86 5da5b5938ddde3e6 6257c02acdf8fd3b
62d07ddcd4e8626f 6423d61bc021f2c5 68d4489ee559b472 6a312a9b59417681
6cfbde668d339194 70d41a9edeb1ab62 71c0c7f7989301e0 72fb375a23c5c30d
73e49ad1920b7247 74b5308380eceeb2 7582793a2046b476 7875072c83099e64
794abc923ce8ae7b 7a70a201496dcf09 820fedc12f7c411d 8687fdeac707ef39
878eb147753d9752 887d07bde37635cd 888b646125305f82 89d9f4de8cd83bc0
8bbcfc5ef041b4d1 8d593d1e44882439 8d8eaadbea1e0e6b 8e28e7caa035bb2e
8e410f2a6702f44b 90d7eca984085e4f 93d4a04b98063189 94038cb7dd941eb0
9efa21d24a61f890 a175827dc46f5185 a38f3bd25218bf11 a3eba0f9d2d981d6
a41ea6b50ab6077b a878e7d4638a9f57 a8d478b5ddd42491 a9b58705ca106c2a
ac2dd6b64541323d b01f65c5eee57ccb ba4250e7b402ef87 ba5ad447551ade4a
bb453a3a3c1de9df bd04ca4ba3f73aed bdd7713581cc79ed be6786485b6ea26b
cb7472ebf86a801d cc4f90c34dc82b18 ccc2de5407f0b248 cf96752f3f331088
d1431d860b9f4f9f d9eae8f569b22a0b db75db4da1cd53fd ddbd3349eb3a8e10
e2b4c2cd06694258 e840a556b6d87a52 e8f2ed7569e503bd e969cf86553a4e64
ec353e0459670f0b ecff9d4866de8089 ef2e9c67b244c499 f3cf7a16576d97c4
f9d2e8bf0c3996e0 f9f4fb8408af37c9 fa5932b0b777a54f fb45c83e3a4dd8d8
fc493d8d1d00b0b3 fd3d15db6d964346 fe502ff31abba977
""".split())

PARAGRAPH_HASHES = frozenset("""
06ec8994e3f52dcc 0711d216ab2b8bf2 1f5f63c611e5005b 22a351f68ff169af
2bfd6e98650a229d 2fdf390bbc55d931 362151e15cefb3ea 40f1da94b891aa4d
458402cd00d26e94 4628bb2f8749c651 48358f6aadaf7ab1 4badd630e88d5c2c
52012cb05b437941 5637cc77166140bb 5c5696c6cffaabb0 5cbd2f5a46d29e5c
71c0c7f7989301e0 823c045250cc96aa 98620343d65e37ae 9d998fbf44912290
9fbe272735f1f23a a74cbe49a4b79fe3 a9f7e1dd8959c98f af83faf042e268a2
bc6cbb0cae225603 c92c0449d187ca80 dfcdc86d0597c399 e663e1f3e7991c7e
f3c107d8d96a6441 f83152913db0b519 fe8cf073a652b4e9
""".split())

# (one per paragraph, a blank line between; wrapped)
FINGERPRINTS = frozenset("".join(fp.split()) for fp in """
083bbda0b76a6c6210c2b9778cf6b658

083bbda0b76a6c6210c2b9778cf6b658f81db97723bf1f6412165adbbec1cbd238a9c383
d6d17585def1

083bbda0b76a6c626201baf30ae4bd7c7fd2

151e12ea9390b76a5829b97733286327b76a3ddae975b977a7a4a610ca97683b29a1f480
f1cfc904dba3cbe5466ecbe5b76ab977b76a2839ca97faa263470695b808dba3da2fb76a
b74eca972af7a7a4a561a5bab76aaff6b977a5ba0f312839ca97b76a7b1af379b76abb29
ca97a81b4c4e7175254b769223cc4813fc1cca9789b6ed6165a094784813fc1cca97e506
1bc0a11a4813e57ee5bd4813fc1cea32a11a48130695ca97754ce5bd3f3ac0c7b76a1b16
ea3206efcc83ccdce6229526b656ea32765d0fc4ab276201ea10a1b7

6197b76ad90e66a463472839ca97b76a8e7ffa519390aa335d2dbe09faa26dab

80017585663e7585fa51ae44a61028398ee4582966a4c7ffd6ac990cfaa21a52ea323bed
fb5aca97b3a1d1d59e847585663eb9770967be099e841216ca97ba63

80017585663ea1169e84bda0b97709675d2dd767ca97fdcf17b2b426582966a4c7ff2206
a318560ca07a7585663ea700dfc1a57505996201fb6056aaf9055829b9770d6e23bf63e6
6eb9663ecae69f02b977b8412bcb308fb9773fc48a836b5d81dd7585663eca97327f5e05
47d8a57505998a5e4fe456aa10c28e975829b9770d6e8e756201a71b0c245829b977b841
2bcba1d9b977dc81ff881c63f4bfca973bb62eb3619775856a2b8e7f824981dcbda010c2
b8ba0f508e7fba786497aa33f7f0b9777770fa51b19bbe09edb4b8d3d34e

80017585663ea1169e84bda0b97709675d2dd767ca97fdcf17b2b426582966a4c7ff2206
a318560ca07a7585663ea700dfc1a57505998a5efb6056aaf9055829b977d66c0d6e612a
b977b8412bcb308fb9773fc48a836b5d81dd7585663eca97327f5e0547d8a57505998a5e
4fe456aa10c28e976201a71b5829b977b841de1c619775856a2b8e7f824981dcbe09b8ba
1f63

80017585663ea1169e84bda0b97709675d2dd767ca97fdcf17b2b426582966a4c7ff2206
a318560ca07a7585663ea700dfc1a57505998a5efb6056aaf9055829b977d66c0d6e612a
b977b8412bcb308fb9773fc48a836b5d81dd7585663eca97327f5e0547d8a57505998a5e
4fe456aa10c28e976201a71b5829b977b841de1c619775856a2b8e7f824981dcbe09b8ba
1f6354c9ca973e234a2b3fc41d958a83ba7880ed59fcbe095b72f130ea1079ad342cbda0
0599fa51ca97a623ddc46f325fb664af649760be36c5d0b493901a29eff3b977211ecc6c
781110c2b977d543

80017585663ea1169e84bda0b97709675d2dd767ca97fdcf17b2b426582966a4c7ff2206
a318560ca07a7585663ea700dfc1a57505998a5efb6056aaf9055829b977d66c0d6e612a
b977b8412bcb308fb9773fc48a836b5d81dd7585663eca97327f5e0547d8a57505998a5e
4fe456aa10c28e976201a71b5829b977b841de1c619775856a2b8e7f824981dcbe09b8ba
1f6354c9ca973e234a2b3fc41d958a83ba7880ed59fcbe095b72f130ea1079ad342cbda0
0599fa51ca97a623ddc46f325fb664af649760be36c5d0b493901a29eff3b977211ecc6c
781110c2b977d5439e847585663e51a418d4ca972c6b12eab8d38e7f0e87d6ac975eb977
5a4562019f826ef45fb6b8d3b977581bca97276458299da1f395b9770e87fa51ae445d2d
c8a6b65586c4faa279f0b977cba04eacaa9e48293d791216ca970e872ad8fa5196f0

9e84cba0115051a418d4ca972c6b12eab8d38e7f0e8781dd7585663eca97327f5e0542b8
76929f8208e4a71b5829b977b8412bcb

a07a7585663e3fc48a83283976923ffe76929f8227cae01fb977cae68ae254c9ca973e23
3fc41d958a838e7fba7880ed59fc

baf3bda0151e12ea9390b76a5829b9779f996327b76a3ddae975b977a7a4eab7a610683b
baf3a3b15724337a2f046eb9b9779ca01a7bbe092c6b8082a7e2ca970e2bb3a2f4bfb977
65420599a68cbf713102b76aab27b977ab27e7a22839b977310289b3d46ad5b4da2fb76a
b74eca97b3a155ef2c6b58a80695ae44d593b8d3a7a4c9fdba7890c475a2f4bf83f8f1cf
c904dba3cbe5466ecbe5b76ab17d47f0ca97faa2634755efc9049150b808dba3620155ef
466efa51466ec76e38092835d3fbb76abe09d60f385c8e7f4e5ff905b8d3b9773928c632
8e489ebbca97e671d9a7

baf3bda0151e12ea9390b76a5829b9779f996327b76a3ddae975b977a7a4eab7a610683b
baf3a3b15724337a2f046eb9b9779ca01a7bbe092c6b8082a7e2ca970e2bb3a2f4bfb977
65420599a68cbf71da2fb76ab74eca97b3a155ef2c6b58a80695ae44d593b8d3a7a4c9fd
ba7890c475a2f4bf7b9606959390ac6bf4bf7feca7a4f3c7ca97a4d22c6bc33fe664b977
a561b8d30e0ff1cfc904dba3cbe5466ecbe5b76ab17d47f0ca97faa2634755efc9049150
b808dba3620155ef466efa51466ec76e38092835d3fbb76abe09d60f385c8e7f4e5ff905
b8d3b9773928c6328e489ebbca97e671d9a7a5bab76aaff6b977a5ba0f31b481b9770fc4
a1b7fa518810d6acb2e50d22ea32e0e4593559f5fb5a71757f9a10c23e1e6567702f5724
71b9

baf3bda0151e12ea9390b76a5829b9779f996327b76a3ddae975b977a7a4eab7a610683b
baf3a3b15724337a2f046eb9b9779ca01a7bbe092c6b8082a7e2ca970e2bb3a2f4bfb977
65420599a68cbf71da2fb76ab74eca97b3a155ef2c6b58a80695ae44d593b8d3a7a4c9fd
ba7890c475a2f4bf83f8f1cfc904dba3cbe5466ecbe5b76ab17d47f0ca97faa2634755ef
c9049150b808dba3620155ef466efa51466ec76e38092835d3fbb76abe09d60f385c8e7f
4e5ff905b8d3b9773928c6328e489ebbca97e671d9a7

baf3bda0151e12ea9390b76a5829b9779f996327b76a3ddae975b977a7a4eab7a610683b
baf3a3b15724337a2f046eb9b9779ca01a7bbe092c6b8082a7e2ca970e2bb3a2f4bfb977
65420599a68cbf71da2fb76ab74eca97b3a155ef2c6b58a80695ae44d593b8d3a7a4c9fd
ba7890c475a2f4bf83f8f1cfc904dba3cbe5466ecbe5b76ab17d47f0ca97faa2634755ef
c9049150b808dba3620155ef466efa51466ec76e38092835d3fbb76abe09d60f385c8e7f
4e5ff905b8d3b9773928c6328e489ebbca97e671d9a7a5bab76aaff6b977a5ba0f31b481
b9770fc4a1b7fa518810d6acb2e50d22ea32e0e4593559f5fb5a71757f9a10c23e1e6567
702f572471b9

e4d64c14bda0bf0b7175ce600d5e6983a3302affb977acbaab5a2006b427b76a15eaca97
5c73923f8d3ca316f379b76abb29ca97a81bbd49d987254bf25423cc4813fc1cca9789b6
ed6165a0c8cac92715ea26d3254bca970011f49d94784813fc1c9ececa97e5061bc0a11a
481344bae5bd4813fc1cea32a11a48130695ca97754ce5bd3f3abe09dcd37585dee3f056
fa51b977c6c10035dfb9c0c7b76a1b16ea3206efcc83ccdcb481b977d46a961e254b0895
2ad861f6b977a11a7175eaade6229526a1b7ea32765d0fc4ab276201ea10a1b7d987d90e
ca975c73f25480015694b76afbb29b82da2fda13bbe9a7e2b977a01a03476db7d70aca97
6347be09caa756946b84ea1028f1

fa3b8accc192a426b97759fc58294d04dd4876927369b427ac6b5829b54cca97a610663e
3316f4bfae449c553fc48a838e7fba7876925d2db482ba7890fbca97b76a663e49ccca97
2c6b12eab8d3ca977a366201baf3ea32d46a9a43b0c2f81dca97acba266d93907369dd1d
1ea5ba7834f7ca97edb4f153f3959740ca9738a959db7861b9c0fa51ca977369411cb4dc
c7a7b97778618d33663ea3b136c548293d6e308f1eb700d96201ca97c44b925188c7ca97
38a98aca06955adb66a481f7a973a610
""".strip().split("\n\n"))

_PRINTS: Tuple[Tuple[str, ...], ...] = tuple(
    tuple(fp[i:i + 4] for i in range(0, len(fp), 4))
    for fp in sorted(FINGERPRINTS))
