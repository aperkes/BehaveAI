"""
config_watcher.py

Utilities to detect when the motion-related settings in a BehaveAI_settings.ini
have changed (without running regeneration), to run the Regenerate_annotations.py
script on demand, and to save a copy of the settings file alongside a trained
model so that later runs compare to that snapshot.

New behaviour requested by user:
- A fast check function `check_settings_changed()` which only returns True/False
  depending on whether a saved snapshot exists and differs from the current config.
  It does NOT call the regeneration script.
- A separate `run_regeneration()` function to explicitly run the regeneration
  script when the user chooses to do so (for example from inside maybe_retrain).

Functions exported:
- read_motion_settings(config_path) -> dict
- settings_different(a, b) -> bool
- find_saved_config(search_glob='**/saved_settings.ini') -> str|None
- check_settings_changed(current_config_path='BehaveAI_settings.ini', saved_config_path=None, model_dirs=None) -> bool
	-> Returns True if snapshot missing or different (does NOT run regeneration).
- run_regeneration(regen_script='Regenerate_annotations.py', regen_args=None, timeout=None) -> int
	-> Runs the regeneration script and returns its return code.
- save_config_with_model(model_project_path, config_path='BehaveAI_settings.ini', saved_name='saved_settings.ini')
	-> Saves a copy of the config alongside a model.

"""

from __future__ import annotations
import configparser
import os
import sys
import shutil
import glob
import subprocess
from typing import Dict, List, Optional, Any

# Keys we consider relevant for deciding whether to regenerate motion annotations
_RELEVANT_KEYS = [
	'frame_skip',
	'motion_threshold',
	'strategy',
	'chromatic_tail_only',
	'expA',
	'expB',
	'lum_weight',
	'rgb_multipliers'
]

FLOAT_TOL = 1e-6

def _any_motion_model_exists(model_dirs: Optional[List[str]] = None) -> bool:
	"""
	Return True if there appears to be at least one built motion model in the workspace.
	We detect a built model by looking for a 'train/weights/best.pt' file inside
	directories whose path contains the substring 'motion' OR inside user-supplied model_dirs.
	"""
	# If explicit model_dirs provided, test those first
	if model_dirs:
		for d in model_dirs:
			# allow the user to supply globs
			for match in glob.glob(d, recursive=True):
				candidate = os.path.join(match, 'train', 'weights', 'best.pt')
				if os.path.exists(candidate):
					return True

	# Generic fallback: look for any path matching *motion*/train/weights/best.pt
	# (recursive search)
	matches = glob.glob('**/*motion*/train/weights/best.pt', recursive=True)
	if matches:
		return True

	# No motion model files found
	return False

	
def _parse_rgb_multipliers(s: str) -> List[float]:
	# Accepts comma separated floats; strip whitespace
	if s is None:
		return []
	parts = [p.strip() for p in s.split(',') if p.strip()]
	return [float(p) for p in parts]


def read_motion_settings(config_path: str) -> Dict[str, Any]:
	"""Read the relevant motion settings from an .ini file and return a normalised dict."""
	if not os.path.exists(config_path):
		raise FileNotFoundError(f"Config file not found: {config_path}")
	cp = configparser.ConfigParser()
	cp.read(config_path)
	d: Dict[str, Any] = {}
	sec = cp['DEFAULT'] if 'DEFAULT' in cp else cp[cp.sections()[0]]

	# Use safe lookups and normalise types
	d['frame_skip'] = int(sec.get('frame_skip', '0'))
	d['motion_threshold'] = int(sec.get('motion_threshold', '0'))
	d['strategy'] = sec.get('strategy', 'exponential').strip().lower()
	d['chromatic_tail_only'] = sec.get('chromatic_tail_only', 'false').strip().lower()
	d['expA'] = float(sec.get('expA', '0.5'))
	d['expB'] = float(sec.get('expB', '0.8'))
	d['lum_weight'] = float(sec.get('lum_weight', '0.7'))
	d['rgb_multipliers'] = _parse_rgb_multipliers(sec.get('rgb_multipliers', '1.0,1.0,1.0'))
	return d


def settings_different(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
	"""Return True if any of the relevant settings differ between dictionaries a and b.

	Comparison rules:
	  - strings compared case-insensitively where appropriate
	  - floats compared within FLOAT_TOL
	  - rgb_multipliers compared element-wise with FLOAT_TOL
	"""
	for k in _RELEVANT_KEYS:
		if k not in a or k not in b:
			# If a key missing from one side, treat as different
			return True
		va = a[k]
		vb = b[k]
		# handle lists
		if isinstance(va, list) and isinstance(vb, list):
			if len(va) != len(vb):
				return True
			for x, y in zip(va, vb):
				if abs(float(x) - float(y)) > FLOAT_TOL:
					return True
			continue
		# handle numeric comparison
		try:
			fa = float(va)
			fb = float(vb)
			if abs(fa - fb) > FLOAT_TOL:
				return True
			else:
				continue
		except Exception:
			pass
		# fallback string compare
		if str(va).strip().lower() != str(vb).strip().lower():
			return True
	return False


def find_saved_config(search_glob: str = '**/saved_settings.ini') -> Optional[str]:
	"""Find a saved snapshot config file in the workspace. Returns the path to the
	newest saved_settings.ini if any found, otherwise None.
	"""
	matches = glob.glob(search_glob, recursive=True)
	if not matches:
		return None
	# pick the newest by mtime
	latest = max(matches, key=os.path.getmtime)
	return latest


def save_config_with_model(model_project_path: str, config_path: str = 'BehaveAI_settings.ini', saved_name: str = 'saved_settings.ini') -> str:
	"""Copy the active config into the model project directory as a snapshot.

	Returns the path to the saved snapshot.
	"""
	if not os.path.exists(config_path):
		raise FileNotFoundError(f"Config not found: {config_path}")
	os.makedirs(model_project_path, exist_ok=True)
	dest = os.path.join(model_project_path, saved_name)
	shutil.copy2(config_path, dest)
	return dest


def run_regeneration(regen_script: str = 'Regenerate_annotations.py', regen_args: Optional[List[str]] = None, timeout: Optional[int] = None) -> int:
	"""Run the regeneration script using the same python interpreter.
	Returns the subprocess returncode (0 = success, other = error).
	"""
	if regen_args is None:
		regen_args = []
	cmd = [sys.executable, regen_script] + regen_args
	try:
		proc = subprocess.run(cmd, check=False, timeout=timeout)
		return proc.returncode
	except subprocess.TimeoutExpired:
		return -999
	except FileNotFoundError:
		# Script not found
		return -1


def check_settings_changed(current_config_path: str = 'BehaveAI_settings.ini',
						   saved_config_path: Optional[str] = None,
						   model_dirs: Optional[List[str]] = None) -> bool:
	"""
	Check whether the relevant motion settings have changed since the last
	saved snapshot.

	Behaviour changes compared to the earlier version:
	  - If no saved snapshot exists, we will now look for existing built motion models.
		* If motion models exist and there is no saved snapshot -> return True
		  (this indicates an existing model but missing snapshot — treat as changed).
		* If motion models do NOT exist (i.e. model hasn't been built yet) -> return False
		  (do not trigger regeneration).
	"""
	current = read_motion_settings(current_config_path)

	saved_path = saved_config_path
	if saved_path is None:
		if model_dirs:
			candidates = []
			for d in model_dirs:
				p = os.path.join(d, 'saved_settings.ini')
				if os.path.exists(p):
					candidates.append(p)
			if candidates:
				saved_path = max(candidates, key=os.path.getmtime)
	if saved_path is None:
		saved_path = find_saved_config()

	if saved_path is None or not os.path.exists(saved_path):
		# No snapshot found – but only treat this as 'changed' if a motion model already exists
		if _any_motion_model_exists(model_dirs):
			# there is an existing model but no snapshot -> we should treat that as changed
			return True
		# no model exists yet (first build) -> do NOT treat as changed
		return False

	saved = read_motion_settings(saved_path)
	return settings_different(current, saved)
	


if __name__ == '__main__':
	# Quick CLI demo: python config_watcher.py --check | --run-regenerate
	import argparse
	parser = argparse.ArgumentParser()
	parser.add_argument('--config', default='BehaveAI_settings.ini')
	parser.add_argument('--saved')
	parser.add_argument('--model-dirs', nargs='*')
	parser.add_argument('--regen', default='Regenerate_annotations.py')
	parser.add_argument('--run', action='store_true', help='Run regeneration script')
	args = parser.parse_args()

	if args.run:
		rc = run_regeneration(args.regen)
		if rc == 0:
			print('Regeneration completed successfully.')
			sys.exit(0)
		else:
			print(f'Regeneration returned code {rc}')
			sys.exit(2)

	changed = check_settings_changed(current_config_path=args.config, saved_config_path=args.saved, model_dirs=args.model_dirs)
	if changed:
		print('-> Motion settings appear to have changed (or no snapshot found).')
		sys.exit(0)
	else:
		print('-> No relevant changes in motion settings.')
		sys.exit(1)
