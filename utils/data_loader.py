# Implements data loading and preprocessing.

import csv
import glob
import imageio
import logging
import matplotlib.pyplot as plt  # for visualizations
import numpy as np
from pad_utils import PadCollate
import pandas as pd
import os
import re
import torch
from torch.utils.data import Dataset, DataLoader


class GestureFramesDataset(Dataset):
	"""Dataset of tensors corresponding to gesture video.

	Args:
		data_dir: str, path to the directory with folders of videos and frames
		transform: list of callable Classes, e.g. from torchvision.transforms,
			with which the video frames should be preprocessed
	"""
	def __init__(self, gesture_labels, data_dir, transform):
		self._labels_to_indices_dict = self.map_labels_to_indices(gesture_labels)
		self._transform = transform
		logging.info('Reindexed labels: {0}'.format(self._labels_to_indices_dict))
		self.data = self.populate_gesture_frames_data(data_dir, gesture_labels)
		self.len = len(self.data)
		logging.info('Initialized a GestureFramesDataset of size {0}.'.format(self.len))
		super(GestureFramesDataset, self).__init__()

	def __getitem__(self, idx):
		# TODO(kenny): Figure out how to sample with balanced labels.
		return self.data[idx]

	def __len__(self):
		return self.len

	def read_frame_tensors_from_dir(self, directory):
		filenames = glob.glob("{0}/*.png".format(directory))
		matches = [re.match('.*_(\d+)\.png', name) for name in filenames]

		# sorted list of (frame_number, frame_path) tuples
		frames = sorted([(int(match.group(1)), match.group(0)) for match in matches])
		sorted_filenames = [f[1] for f in frames] 
		frames_list = []

		for frame_file in sorted_filenames:
			# Returns an (H, W, C) shaped tensor, so transpose it to (C, H, W)
			frame_ndarray = imageio.imread(frame_file).transpose(2, 0, 1)
			frame_ndarray = self._transform(frame_ndarray)
			frames_list.append(frame_ndarray)

		# Stacks up to a (C, T, H, W) tensor.
		return torch.stack(frames_list, dim=1)

	def populate_gesture_frames_data(self, data_dir, gesture_labels, type_data="kinect"):
		"""Returns a list of dicts with keys:
			'frames': 4D tensor (T, H, W, C) representing the spatiotemporal frames for a video
			'label': y (ground truth)
			'seq_len': number of frames in the video
		"""
		logging.info('Populating frame tensors for {0} specified labels in data dir {1}: {2}'.format(
			len(gesture_labels), data_dir, gesture_labels))

		labels_file = os.path.join(data_dir, '{0}_list.txt'.format(data_dir.split('/')[-1]))
		data = pd.read_csv(labels_file, sep=" ", header=None)
		data.columns = ["rgb", "kinect", "label"]

		label_to_dirs = {}
		for label in gesture_labels:
			directory_labels = data.loc[data['label'] == label][type_data].tolist()
			# strip .avi from the end of the filename
			directories = [''.join(dir_label.split('.avi')[:-1]) for dir_label in directory_labels]
			label_to_dirs[label] = directories

		data = []
		for label, directories in label_to_dirs.items():
			for directory in directories:
				frames = self.read_frame_tensors_from_dir(os.path.join(data_dir, directory))
				data.append({
					'frames': frames,
					'label': self._labels_to_indices_dict[label],
					'seq_len': frames.size(1)
				})

		return data

	@staticmethod
	def map_labels_to_indices(gesture_labels):
		"""Returns a dict mapping the gesture labels to integer class labels."""
		return dict(zip(gesture_labels, range(len(gesture_labels))))


def GenerateGestureFramesDataLoader(gesture_labels, data_dir, max_seq_len,
									batch_size, transform):
	"""Returns a configured DataLoader instance."""

	# Build a gesture frames dataset using the configuration information.
	# This is just dummy code to be replaced.
	transformed_dataset = GestureFramesDataset(gesture_labels, data_dir, transform)
	return DataLoader(transformed_dataset,
		batch_size=batch_size,
		shuffle=True,
		# num_workers=4,  -- uncomment to run in parallel
		collate_fn=PadCollate(max_seq_len, dim=1)  # dim=1 represents timesteps
	)

def GetGestureFramesDataLoaders(data_dirs, model_config):
	"""Returns a tuple consisting of the train, valid, and test GestureFramesDataLoader objects."""
	# TODO: Support pickling to speed up the process. Perhaps we can hash the
	# list of gesture labels to a checksum and check if a file with that name exists.
	return (GenerateGestureFramesDataLoader(model_config.gesture_labels,
		d, model_config.max_seq_len, model_config.batch_size,
		model_config.transform) for d in data_dirs)
