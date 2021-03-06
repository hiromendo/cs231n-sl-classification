# Implements data loading and preprocessing.
import logging
import pandas as pd
import os
import re
import torch
from torch.utils.data import DataLoader

from utils.pad_utils import PadCollate
from utils.gesture_frame_dataset_pickle import GestureFrameDatasetPickle
from utils.gesture_frame_dataset import GestureFrameDataset
from utils.resnet_encoding_dataset import ResnetEncodingDataset
from utils.combination_dataset import CombinationDataset

def GenerateDataLoader(gesture_labels, dataloader_type, data_dir, max_seq_len,
				batch_size, transform, num_workers, max_example_per_label, shuffle=False, 
				pickle_config=False):
	"""Returns a configured DataLoader instance."""

	# Build a dataloader using the configuration information. Subjected to change
	data_type, dataset = dataloader_type.split('-')	

	if os.path.split(data_dir)[1] == 'test':
		logging.info('Remove max_example_per_label for test dataset')
		max_example_per_label = None


	if pickle_config:
		transformed_dataset = GestureFrameDatasetPickle(gesture_labels, data_dir, data_type, transform, max_example_per_label, pickle_config)

		return DataLoader(transformed_dataset,
			batch_size=1,
			shuffle=False,
			num_workers=num_workers
		)

	if dataset == 'image':
		transformed_dataset = GestureFrameDataset(gesture_labels, data_dir, data_type, transform, max_example_per_label)

		return DataLoader(transformed_dataset,
			batch_size=batch_size,
			shuffle=shuffle,
			num_workers=num_workers
		)

	if dataset == 'encoding':
		dataset = ResnetEncodingDataset(gesture_labels, data_dir, data_type, max_example_per_label)

		return DataLoader(dataset,
			batch_size=batch_size,
			shuffle=shuffle,
			num_workers=num_workers,
			collate_fn=PadCollate(dataset.max_seq_len, dim=1),
			pin_memory=True
		)

	if dataset == 'combination':
		dataset = CombinationDataset(gesture_labels, data_dir, data_type, max_example_per_label)

		return DataLoader(dataset,
			batch_size=batch_size,
			shuffle=shuffle,
			num_workers=num_workers,
			collate_fn=PadCollate(dataset.max_seq_len, dim=1),
			pin_memory=True
		)


def GetDataLoaders(data_dirs, model_config):
	"""Returns a tuple consisting of the train, valid, and test dataloaders."""
	pickle_config = None
	if model_config.mode == 'pickle':
		pickle_config = {
			'resnet_num_layers': model_config.resnet_num_layers,
			'pickle_overwrite': model_config.pickle_overwrite,
			'load': model_config.load,
		}

	return [GenerateDataLoader(
		model_config.gesture_labels,
		model_config.dataloader_type,
		data_directory,
		model_config.max_seq_len,
		model_config.batch_size,
		model_config.transform,
		model_config.num_workers or 0,
		model_config.max_example_per_label,
		model_config.shuffle,
		pickle_config,
		) for data_directory in data_dirs]
