Source code for GD Video Maker.

gd_video_maker.py   the window; also runs the fit and inject steps when started with --fit or --build
fit_sharp.py        fits a video with GD objects (also usable on its own: python fit_sharp.py video.mp4)
fit_objects.py      the object search: tries objects, keeps the best, nudges it into place
fit_shapes.py       video reading/writing helpers
build_level.py      writes the result into a level in CCLocalLevels.dat, with a backup and checks
build_library.py    reads sprite art from your GD install
object_audit.py     the in-game test that decided which objects stay still and take color fully
verified_objects.json  the objects that passed
test_*.py           self-checks

First run install_prerequisites.bat (installs Python, FFmpeg, NumPy, PyInstaller).
Then: python gd_video_maker.py to run it, or build_exe.bat to rebuild the .exe.
