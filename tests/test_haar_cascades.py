"""Guard the OpenCV build the scoring pipeline actually needs.

Every other scoring test stubs the signal loader, so none of them construct a
cascade. OpenCV 5 removed CascadeClassifier and ships no cascade XML data, and
an unpinned major let it install — CI stayed green while the worker raised
AttributeError on real footage. These tests touch the real cv2 build.
"""

import pytest

from apps.pipeline.scoring import ScoringError, _haar_cascade

REQUIRED_CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_smile.xml")


@pytest.mark.parametrize("cascade_name", REQUIRED_CASCADES)
def test_required_haar_cascade_loads(cascade_name: str):
    classifier = _haar_cascade(cascade_name)

    assert not classifier.empty()


def test_missing_cascade_raises_scoring_error():
    with pytest.raises(ScoringError, match="Haar cascade unavailable"):
        _haar_cascade("haarcascade_not_a_real_cascade.xml")
