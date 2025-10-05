#!/usr/bin/env python3
import cv2
import os
import numpy as np
import configparser
import glob
from collections import deque

def load_config(config_path):
	"""Load and parse the configuration file"""
	config = configparser.ConfigParser()
	config.read(config_path)
	
	params = {}
	try:
		# Read parameters
		params['scale_factor'] = float(config['DEFAULT'].get('scale_factor', '1.0'))
		params['expA'] = float(config['DEFAULT'].get('expA', '0.5'))
		params['expB'] = float(config['DEFAULT'].get('expB', '0.8'))
		params['strategy'] = config['DEFAULT'].get('strategy', 'exponential')
		params['chromatic_tail_only'] = config['DEFAULT'].get('chromatic_tail_only', 'false').lower()
		params['lum_weight'] = float(config['DEFAULT'].get('lum_weight', '0.7'))
		params['rgb_multipliers'] = [float(x) for x in config['DEFAULT']['rgb_multipliers'].split(',')]
		params['frame_skip'] = int(config['DEFAULT'].get('frame_skip', '0'))
		params['motion_threshold'] = -1 * int(config['DEFAULT'].get('motion_threshold', '0'))
		params['motion_blocks_static'] = config['DEFAULT'].get('motion_blocks_static', 'false').lower()
		params['static_blocks_motion'] = config['DEFAULT'].get('static_blocks_motion', 'false').lower()
		params['save_empty_frames'] = config['DEFAULT'].get('save_empty_frames', 'false').lower()
		
		# Compute base frame window size (number of sampled frames)
		base_window = 4
		if params['strategy'] == 'exponential':
			if params['expA'] > 0.2 or params['expB'] > 0.2:
				base_window = 5
			if params['expA'] > 0.5 or params['expB'] > 0.5:
				base_window = 10
			if params['expA'] > 0.7 or params['expB'] > 0.7:
				base_window = 15
			if params['expA'] > 0.8 or params['expB'] > 0.8:
				base_window = 20
			if params['expA'] > 0.9 or params['expB'] > 0.9:
				base_window = 45
		# store both: base sampled frames and total frames to read
		params['base_frame_window'] = base_window
		params['frame_window'] = base_window * (params['frame_skip'] + 1)
		
	except KeyError as e:
		raise KeyError(f"Missing configuration parameter: {e}")
	
	return params

def generate_base_images(video_path, frame_num, params):
	"""
	Generate base static and motion images for a specific video frame
	using the same processing as the annotation tool.

	Interpretation: `frame_num` is the LAST frame of the motion window.
	We sample `base_N = params['base_frame_window']` frames every `step = frame_skip+1`,
	so the frames used are:
	    start_frame, start_frame + step, ..., start_frame + (base_N-1)*step
	where the last appended frame should equal frame_num (if possible).
	"""
	cap = cv2.VideoCapture(video_path)
	if not cap.isOpened():
		print(f"Error opening video: {video_path}")
		return None, None
	
	total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
	if total_frames <= 0:
		print(f"Video appears empty or unreadable: {video_path}")
		cap.release()
		return None, None

	# Parameters for sampling
	step = params['frame_skip'] + 1
	base_N = params.get('base_frame_window', 4)

	# Compute intended start frame so that last appended index = frame_num
	start_frame = int(frame_num - (base_N - 1) * step)
	start_frame = max(0, start_frame)  # clamp to 0
	if start_frame > total_frames - 1:
		# nothing we can do
		print(f"Start frame {start_frame} is beyond video length ({total_frames}) for {video_path}")
		cap.release()
		return None, None

	# We'll read forward from start_frame and append every 'step' frames until we have base_N frames
	cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
	collected = []
	read_count = 0
	idx = start_frame
	while len(collected) < base_N and idx <= total_frames - 1:
		ret, frame = cap.read()
		if not ret:
			break
		# append only every 'step' frames
		if (read_count % step) == 0:
			if params['scale_factor'] != 1.0:
				frame = cv2.resize(frame, None, fx=params['scale_factor'], fy=params['scale_factor'])
			collected.append(frame.copy())
		read_count += 1
		idx += 1
		# safety to prevent infinite loop
		if read_count > params['frame_window'] + 10:
			break

	# If we couldn't collect any frames, fail
	if not collected:
		cap.release()
		print(f"Could not collect frames for target {frame_num} (start {start_frame})")
		return None, None

	# If we collected fewer than base_N frames, we still try to compute motion from what we have.
	# We'll mimic the annotator's prev_frames/diff update behavior: initialize prev_frames to first gray,
	# then iterate through the rest updating prev_frames and computing diffs for the final frame.
	prev_frames = [None] * 3
	static_img = None
	diffs = None
	gray = None

	for i, f in enumerate(collected):
		if f is None:
			continue
		# prepare frame
		frame_bgr = f
		gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

		if static_img is None:
			# initialize
			static_img = frame_bgr.copy()
			prev_frames = [gray.copy()] * 3
			# continue to next frame to allow diffs to get meaningful values
			continue

		# compute diffs relative to three prev frames
		current_diffs = [cv2.absdiff(prev_frames[j], gray) for j in range(3)]

		# update prev_frames according to strategy
		if params['strategy'] == 'exponential':
			prev_frames[0] = gray
			prev_frames[1] = cv2.addWeighted(prev_frames[1], params['expA'], gray, 1 - params['expA'], 0)
			prev_frames[2] = cv2.addWeighted(prev_frames[2], params['expB'], gray, 1 - params['expB'], 0)
		elif params['strategy'] == 'sequential':
			prev_frames[2] = prev_frames[1]
			prev_frames[1] = prev_frames[0]
			prev_frames[0] = gray

		# store the diffs for the most recent processed frame
		static_img = frame_bgr.copy()
		diffs = current_diffs

	# we want diffs corresponding to the last frame of the window (i.e. frame_num)
	# If we don't have diffs (not enough frames), we can't build motion image reliably.
	if diffs is None or gray is None:
		cap.release()
		print(f"Insufficient frames to compute diffs for {frame_num} (collected {len(collected)} frames)")
		return None, None

	# Build motion image using the same algorithm as annotator
	if params['chromatic_tail_only'] == 'true':
		tb = cv2.subtract(diffs[0], diffs[1])	
		tr = cv2.subtract(diffs[2], diffs[1])
		tg = cv2.subtract(diffs[1], diffs[0])
				
		blue = cv2.addWeighted(gray, params['lum_weight'], tb, params['rgb_multipliers'][2], params['motion_threshold'])
		green = cv2.addWeighted(gray, params['lum_weight'], tg, params['rgb_multipliers'][1], params['motion_threshold'])
		red = cv2.addWeighted(gray, params['lum_weight'], tr, params['rgb_multipliers'][0], params['motion_threshold'])
	else:
		blue = cv2.addWeighted(gray, params['lum_weight'], diffs[0], params['rgb_multipliers'][2], params['motion_threshold'])
		green = cv2.addWeighted(gray, params['lum_weight'], diffs[1], params['rgb_multipliers'][1], params['motion_threshold'])
		red = cv2.addWeighted(gray, params['lum_weight'], diffs[2], params['rgb_multipliers'][0], params['motion_threshold'])

	motion_img = cv2.merge([blue, green, red]).astype(np.uint8)

	cap.release()
	return static_img, motion_img

def read_mask_file(mask_path):
	"""Read grey box coordinates from mask file"""
	boxes = []
	if os.path.exists(mask_path):
		with open(mask_path, 'r') as f:
			for line in f:
				parts = line.strip().split()
				if len(parts) == 4:
					boxes.append(tuple(map(int, parts)))
	return boxes

def apply_grey_boxes(image, boxes):
	"""Apply grey boxes to an image"""
	result = image.copy()
	for (x1, y1, x2, y2) in boxes:
		cv2.rectangle(result, (x1, y1), (x2, y2), (128, 128, 128), -1)
	return result

def apply_blocking_boxes(image, boxes):
	"""Apply blocking boxes to an image"""
	result = image.copy()
	for (x1, y1, x2, y2) in boxes:
		cv2.rectangle(result, (x1, y1), (x2, y2), (128, 128, 128), -1)
	return result

def get_blocking_boxes(label_path, img_w, img_h):
	"""Convert normalized label coordinates to absolute coordinates"""
	boxes = []
	if os.path.exists(label_path):
		with open(label_path, 'r') as f:
			for line in f:
				parts = line.split()
				if len(parts) < 5: 
					continue
				# Parse normalized coordinates
				xc = float(parts[1]); yc = float(parts[2])
				w = float(parts[3]); h = float(parts[4])
				# Convert to absolute coordinates
				x1 = int((xc - w/2) * img_w)
				y1 = int((yc - h/2) * img_h)
				x2 = int((xc + w/2) * img_w)
				y2 = int((yc + h/2) * img_h)
				boxes.append((x1, y1, x2, y2))
	return boxes

def regenerate_annotations(config_path):
	"""Main function to regenerate annotation images"""
	# Load configuration
	params = load_config(config_path)
	
	# Collect all unique base names (video_frame combinations) from motion labels
	base_dirs = [
		('annot_motion', ['train', 'val'])
	]
	
	# Collect all unique base names (video_frame combinations)
	base_names = set()
	for base_dir, splits in base_dirs:
		for split in splits:
			label_dir = os.path.join(base_dir, 'labels', split)
			if not os.path.exists(label_dir):
				continue
			label_files = glob.glob(os.path.join(label_dir, '*.txt'))
			for label_file in label_files:
				if label_file.endswith('.mask.txt'):
					continue  # Skip mask files
				base_name = os.path.splitext(os.path.basename(label_file))[0]
				base_names.add((base_name, split, base_dir))
	
	# Process each unique frame
	for base_name, split, base_dir in base_names:
		# Extract video name and frame number (frame number stored as LAST frame of window)
		parts = base_name.split('_')
		try:
			frame_num = int(parts[-1])
		except ValueError:
			print(f"Skipping {base_name}: trailing token is not an integer")
			continue
		video_name = '_'.join(parts[:-1])
		
		# Find video file
		video_path = None
		clips_dir = 'clips'
		for ext in ['.mp4', '.avi', '.mov', '.mkv', '.MP4', '.AVI', '.MOV', '.MKV']:
			test_path = os.path.join(clips_dir, video_name + ext)
			if os.path.exists(test_path):
				video_path = test_path
				break
		
		if not video_path:
			print(f"Video not found: {video_name}")
			print(f"Expecting video files ending with .mp4, .avi, .mov, or .mkv in /clips/ directory")
			continue
		
		# Generate base images: frame_num is interpreted as the LAST frame of the window
		static_img, motion_img = generate_base_images(video_path, frame_num, params)
		if static_img is None:
			print(f"  Could not generate images for {base_name}")
			continue
		
		# Get image dimensions
		img_h, img_w = static_img.shape[:2]
		
		# Get mask paths
		static_mask_path = os.path.join('annot_static', 'masks', split, f"{base_name}.mask.txt")
		motion_mask_path = os.path.join('annot_motion', 'masks', split, f"{base_name}.mask.txt")
		
		# Read mask files
		static_mask_boxes = read_mask_file(static_mask_path)
		motion_mask_boxes = read_mask_file(motion_mask_path)
		
		# Get label paths
		static_label_path = os.path.join('annot_static', 'labels', split, f"{base_name}.txt")
		motion_label_path = os.path.join('annot_motion', 'labels', split, f"{base_name}.txt")
		
		# Process motion image
		if base_dir == 'annot_motion' or params['save_empty_frames'] == 'true':
			if motion_img is None:
				print(f"  Could not generate motion image for {base_name}")
			else:
				motion_final = motion_img.copy()
				
				# Apply grey boxes
				motion_final = apply_grey_boxes(motion_final, motion_mask_boxes)
				
				# Apply static blocking if enabled
				if params['static_blocks_motion'] == 'true':
					static_boxes = get_blocking_boxes(static_label_path, img_w, img_h)
					motion_final = apply_blocking_boxes(motion_final, static_boxes)
				
				# Save motion image
				motion_img_path = os.path.join('annot_motion', 'images', split, f"{base_name}.jpg")
				os.makedirs(os.path.dirname(motion_img_path), exist_ok=True)
				cv2.imwrite(motion_img_path, motion_final)
				print(f"Regenerated motion: {motion_img_path}")

if __name__ == "__main__":
	config_path = 'BehaveAI_settings.ini'
	if not os.path.exists(config_path):
		print(f"Config file not found: {config_path}")
	else:
		regenerate_annotations(config_path)
	print("Regeneration complete!")
