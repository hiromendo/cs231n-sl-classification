"""Runs the gesture classification model, either in train or testing mode
according to config.py."""

import logging
import numpy as np
import os
import random
import sys
import time

from configs import config
from configs.config import MODEL_CONFIG
from utils import data_loader, checkpoint_loader, pickle_encoding, sweeper
from trainer import train_utils
from utils.metrics import PredictionSaver

import torch
import torch.nn.functional as F


DATA_DIRS = [config.TRAIN_DATA_DIR, config.VALID_DATA_DIR, config.TEST_DATA_DIR]

def run_experiment_with_config(model_config, train_dataloader, valid_dataloader, test_dataloader):
	# Initialize the model, or load a pretrained one.
	model = MODEL_CONFIG.model(model_config)
	lossdatapoints = []
	accuracy_datapoints = []

	# Set the loss fn
	loss_fn = model_config.loss_fn
	# Random seeds
	random.seed(model_config.seed)
	
	# load checkpoint if available
	if model_config.load:
		if model_config.checkpoint_to_load:
			logging.info('Loading checkpoint from {0}'.format(model_config.checkpoint_to_load))
			checkpoint_full_path = os.path.join(model_config.checkpoint_path, model_config.checkpoint_to_load)
			model.load_checkpoint(checkpoint_full_path)
		else:
			logging.info('--checkpoint_to_load argument was not given. Searching for the model with the best acc.')
			best_checkpoint = checkpoint_loader.get_best_checkpoint(model_config)
			if best_checkpoint:
				model.load_checkpoint(best_checkpoint)
			else:
				logging.info('Best checkpoint cannot be found. Program will now terminate.')
				return

	elif model_config.mode == 'test':
		raise ValueError('Testing the model requires --load flag and --checkpoint_to_load argument.')

	# activate cuda if available and enable
	parallel_model = model
	if model_config.use_cuda:
		if torch.cuda.is_available():
			logging.info('Running the model using GPUs. (--use_cuda)')
			# passing model into DataParallel allows for data to be
			# computed in parallel through all the available GPUs
			parallel_model = torch.nn.DataParallel(model)
			model.cuda()
			loss_fn.cuda()
			# set cuda seeds
			torch.cuda.manual_seed_all(model_config.seed)
		else:
			logging.info('Sorry, no GPUs are available. Running on CPU.')

	if model_config.mode == 'pickle':
		logging.info('The model will now commence pickling')
		with torch.no_grad():
			pickle_encoding.pickle_encoding(DATA_DIRS, model_config, parallel_model)
			return

	# Train the model.
	if model_config.mode == 'train':
		logging.info("Model will now begin training.")
		checkpoint_epoch = 0
		if model_config.checkpoint_to_load:
			checkpoint_epoch = model.training_epoch

		for epoch in range(checkpoint_epoch, model_config.epochs + checkpoint_epoch):
			mean_loss, train_acc = train_utils.train_model(model=parallel_model,
									dataloader=train_dataloader,
									# TODO: Pass class weights array to the loss function if it's CE.
									loss_fn=loss_fn,
									optimizer=model_config.optimizer_fn(
										# Allows the model to freeze certain parameters
										# by setting requires_grad=False.
										filter(lambda x: x.requires_grad,
											parallel_model.parameters()),
										lr=model_config.learning_rate,
										weight_decay=model_config.weight_decay
									),
									epoch=epoch,
									is_lstm=model_config.is_lstm,
									use_cuda=model_config.use_cuda,
									verbose=model_config.verbose)
			model_config.train_loss_saver.update([epoch, np.around(mean_loss, 3)])

			if model_config.use_cuda:
				train_acc_np = train_acc.cpu().numpy()
			else:
				train_acc_np = train_acc.numpy()

			model_config.train_acc_saver.update([epoch, np.around(train_acc_np, 3)])

			# save the model after every epoch
			if model_config.save_every_epoch:
				model.save_to_checkpoint(model_config.checkpoint_path)

			if epoch % model_config.validate_every == 0:
				with torch.no_grad():
					val_acc = train_utils.validate_model(model=parallel_model,
														dataloader=valid_dataloader,
														loss_fn=loss_fn,
														is_lstm=model_config.is_lstm,
														use_cuda=model_config.use_cuda)

				logging.info('Validation Acc: {:.2f}%'
					.format(val_acc))

				if model_config.use_cuda:
					val_acc_np = val_acc.cpu().numpy()
				else:
					val_acc_np = val_acc.numpy()
				model_config.valid_acc_saver.update([epoch, np.around(val_acc_np, 3)])

				# Check if current validation accuracy exceeds the best accuracy
				best_acc = float(model.best_accuracy)
				curr_acc = float(val_acc)
				if best_acc < curr_acc:
					model.best_accuracy = curr_acc
					model.save_to_checkpoint(model_config.checkpoint_path, is_best=True)
		
			# Update model epoch number and accuracy
			model.training_epoch += 1

		# Save the final model to a checkpoint.
		model.save_to_checkpoint(model_config.checkpoint_path)

		# Run the model on the test set, using a new test dataloader.
		with torch.no_grad():
			test_acc = train_utils.validate_model(model=parallel_model,
													dataloader=test_dataloader,
													loss_fn=loss_fn,
													is_lstm=model_config.is_lstm,
													use_cuda=model_config.use_cuda,
													predictions_saver=model_config.preds_saver)
		logging.info('Test Acc: {:.2f}%.'.format(test_acc))


def main():
	logging.info('Cmd: python {0}'.format(' '.join(sys.argv)))
	logging.info('Config:\n {0}'.format(MODEL_CONFIG))
	logging.info('Running experiment <{0}> in {1} mode.\n'
		'Description of model: {2}'.format(MODEL_CONFIG.name,
			MODEL_CONFIG.mode, MODEL_CONFIG.description))

	# Initialize a hyperparameter sweeper (set to k=1 for default mode).
	hyp_sweeper = sweeper.HyperparameterSweeper(
		# TODO: Initialize this from the configs.
		config_options = {
			'learning_rate': sweeper.HyperparameterOption(
				sweeper.ValueType.CONTINUOUS, exp_range=(-5, -3), round_to=5),
			'dropout': sweeper.HyperparameterOption(
				sweeper.ValueType.CONTINUOUS, value_range=(0.0, 0.2), round_to=2),
			'weight_decay': sweeper.HyperparameterOption(
				sweeper.ValueType.CONTINUOUS, exp_range=(-4, -2), round_to=2),
		},
		model_config = MODEL_CONFIG,
		metrics_dir = config.METRICS,
		plots_dir = config.PLOTS
	)

	dataloaders = data_loader.GetDataLoaders(DATA_DIRS, MODEL_CONFIG)

	if MODEL_CONFIG.num_sweeps == 0 or MODEL_CONFIG.mode == 'pickle':
		 model_config = hyp_sweeper.get_original_sweep()
		 run_experiment_with_config(model_config, *dataloaders)
	else:
		# Run the model across random hyperparameter settings.
		for model_config in hyp_sweeper.get_random_sweeps(MODEL_CONFIG.num_sweeps):
			logging.info('===== HYPERPARAMETER SWEEP {0}/{1} ====='.format(
				hyp_sweeper.number_of_completed_sweeps(), model_config.num_sweeps))
			logging.info('Hyperparameters swept: {0}'.format(model_config.sampled_config))
			run_experiment_with_config(model_config, *dataloaders)

		hyp_sweeper.analyze_hyperparameter('learning_rate')
		hyp_sweeper.analyze_hyperparameter('dropout')
		hyp_sweeper.analyze_hyperparameter('weight_decay')

	# TODO: Analyze the performance across different sweeps.
	if MODEL_CONFIG.mode != 'pickle':
		hyp_sweeper.analyze_performance()
		# hyp_sweeper.analyze_confusion()	
		hyp_sweeper.analyze_train_vs_valid_accuracy()


if __name__ == '__main__':
	main()
