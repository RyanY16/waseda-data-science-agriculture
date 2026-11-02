# Source preparation scripts

These are the exact scripts used in the original agriculture workspace. They document the derivation of the bundled dashboard data. They are not a standalone data download/training pipeline: their ROOT/HERE paths expect the original workspace layout, raw shapefile and radar files, frozen classifier output and damage lookup. See the root README for those requirements.

The website and JavaScript checks run directly from the checked-in `site/` files without Python or these raw inputs. No training or independent flood-accuracy reproduction is claimed by the web-app build.
