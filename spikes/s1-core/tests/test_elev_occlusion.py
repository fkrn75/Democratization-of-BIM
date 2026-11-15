# SPDX-License-Identifier: MPL-2.0
"""입면 가림 판정: 앞벽의 문 개구부 때문에 띠가 높이로 나뉘어도, 앞벽이 막은 곳 뒤의 선은 띠 이음매 높이에서도 가려진다."""
import sys

import pytest

from conftest import ROOT
from dobim_core import drawing as D

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def ra01(tmp_path):
    sess, ids, views = s3lib.build(tmp_path / "ra01.dobim")
    yield sess, ids, views
    sess.close()


def _lines(sess, v):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, v)


def _head(lines, z=2100):
    return sorted((round(ln[1]), round(ln[3])) for ln in lines if round(ln[2]) == z == round(ln[4]))


def test_앞벽에_가린_뒤벽_문_윗선이_보이는_선으로_남지_않는다(ra01):
    sess, ids, views = ra01
    # 남측 입면: 앞(남측) 외벽에도 문이 있어 벽 띠가 2100 에서 나뉜다. 안쪽 칸막이 벽 두 개의 문 윗선(두께 100, 문 높이 2100)은 앞벽 뒤에 있다.
    heads = _head(_lines(sess, views["elev-S"]))
    assert (3950, 4050) not in heads and (6600, 7400) not in heads
    # 남측 외벽 자신의 문(D-01) 윗선은 개구부 안이라 그대로 보인다
    assert (7550, 8450) in heads
    # 동·서 입면: 안쪽 벽 문 윗선이 외벽 뒤에 가려진다
    assert _head(_lines(sess, views["elev-E"])) == [] and _head(_lines(sess, views["elev-W"])) == []


def test_가림_수정_뒤에도_증분이_전체_재생성과_같다(ra01):
    sess, ids, views = ra01
    for k in ("elev-N", "elev-E", "elev-S", "elev-W", "section-BB"):
        assert _lines(sess, views[k]) == s3lib.full_lines(sess.eng, views[k]), k
