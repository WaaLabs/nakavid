Res10 SSD face detector, vendored from OpenCV's own samples (Apache 2.0,
same license as OpenCV itself) — CPU-only via `cv2.dnn`, no torch/tensorflow.

- `deploy.prototxt` — https://raw.githubusercontent.com/opencv/opencv/4.x/samples/dnn/face_detector/deploy.prototxt
- `res10_300x300_ssd_iter_140000_fp16.caffemodel` — https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180205_fp16/res10_300x300_ssd_iter_140000_fp16.caffemodel

Not pip-installable — these are model weights, not a package — so they're
checked in rather than fetched at deploy time. See `scoring.py`'s
`dnn_face_detector` for how they're loaded.
