"""Runs the gesture classification model, either in train or testing mode
according to config.py."""

import logging
import numpy as np
import random
import sys
import time
import csv

from configs import config
from configs.config import MODEL_CONFIG
from utils import data_loader, checkpoint_loader, pickle_encoding
from trainer import train_utils

import torch
import torch.nn.functional as F


DATA_DIRS = [config.TRAIN_DATA_DIR, config.VALID_DATA_DIR, config.TEST_DATA_DIR]

def main():
	logging.info('Cmd: python {0}'.format(' '.join(sys.argv)))
	logging.info('Config:\n {0}'.format(MODEL_CONFIG))
	logging.info('Running experiment <{0}> in {1} mode.\n'
		'Description of model: {2}'.format(MODEL_CONFIG.name,
			MODEL_CONFIG.mode, MODEL_CONFIG.description))

	# Initialize the model, or load a pretrained one.
	model = MODEL_CONFIG.model(MODEL_CONFIG)
	lossdatapoints =[]
	accuracy_datapoints =[]

	# load checkpoint if available
	if MODEL_CONFIG.load:
		if MODEL_CONFIG.checkpoint_to_load:
			logging.info('Loading checkpoint from {0}'.format(MODEL_CONFIG.checkpoint_to_load))
			checkpoint_full_path = os.path.join(MODEL_CONFIG.checkpoint_path, MODEL_CONFIG.checkpoint_to_load)
			model.load_checkpoint(checkpoint_full_path)
		else:
			logging.info('--checkpoint_to_load argument was not given. Searching for the model with the best acc.')
			best_checkpoint = checkpoint_loader.get_best_checkpoint(MODEL_CONFIG)
			if best_checkpoint:
				model.load_checkpoint(best_checkpoint)
			else:
				logging.info('Best checkpoint cannot be found. Program will now terminate.')
				return

	elif MODEL_CONFIG.mode == 'test':
		raise ValueError('Testing the model requires --load flag and --checkpoint_to_load argument.')


	if MODEL_CONFIG.mode == 'pickle':
		logging.info('The model will now commence pickling')
		pickle_encoding.pickle_encoding(DATA_DIRS, MODEL_CONFIG, model)
		return
	
	(train_dataloader, valid_dataloader, test_dataloader) = data_loader.GetDataLoaders(DATA_DIRS, MODEL_CONFIG)

	# Set the loss fn
	loss_fn = MODEL_CONFIG.loss_fn
	# Random seeds
	random.seed(MODEL_CONFIG.seed)

	# activate cuda if available and enable
	parallel_model = model
	if MODEL_CONFIG.use_cuda:
		if torch.cuda.is_available():
			logging.info('Running the model using GPUs. (--use_cuda)')
			# passing model into DataParallel allows for data to be
			# computed in parallel through all the available GPUs
			parallel_model = torch.nn.DataParallel(model)
			model.cuda()
			loss_fn.cuda()
			# set cuda seeds
			torch.cuda.manual_seed_all(MODEL_CONFIG.seed)
		else:
			logging.info('Sorry, no GPUs are available. Running on CPU.')

	# Train the model.
	if MODEL_CONFIG.mode == 'train':
		logging.info("Model will now begin training.")
		checkpoint_epoch = 0
		if MODEL_CONFIG.checkpoint_to_load:
			checkpoint_epoch = model.training_epoch
		for epoch in range(checkpoint_epoch, MODEL_CONFIG.epochs + checkpoint_epoch):
			mean_loss = train_utils.train_model(model=parallel_model,
									dataloader=train_dataloader,
									loss_fn=loss_fn,
									optimizer=MODEL_CONFIG.optimizer_fn(
										# Allows the model to freeze certain parameters
										# by setting requires_grad=False.
										filter(lambda x: x.requires_grad,
											parallel_model.parameters()),
										lr=MODEL_CONFIG.learning_rate,
										weight_decay=MODEL_CONFIG.weight_decay
									),
									epoch=epoch,
									is_lstm=MODEL_CONFIG.is_lstm,
									use_cuda=MODEL_CONFIG.use_cuda,
									verbose=MODEL_CONFIG.verbose)
			lossdatapoints.append([epoch, mean_loss])



			if epoch % MODEL_CONFIG.log_interval == 0:
				train_acc = train_utils.validate_model(model=parallel_model,
													dataloader=train_dataloader,
													loss_fn=loss_fn,
													is_lstm=MODEL_CONFIG.is_lstm,
													use_cuda=MODEL_CONFIG.use_cuda)

				val_acc = train_utils.validate_model(model=parallel_model,
													dataloader=valid_dataloader,
													loss_fn=loss_fn,
													is_lstm=MODEL_CONFIG.is_lstm,
													use_cuda=MODEL_CONFIG.use_cuda)

				logging.info('Train Epoch: {}\tTrain Acc: {:.2f}%\tValidation Acc: {:.2f}%'
					.format(epoch, train_acc, val_acc))
				accuracy_datapoints.append([epoch, '{:.2f}'.format(train_acc), '{:.2f}'.format(val_acc)])
				#atapoints = [epoch, '{:.2f}'.format(train_acc), train_loss, '{:.2f}'.format(val_acc), val_loss]
				# with open('{0}/{1}_{2}.csv'.format(MODEL_CONFIG.name,MODEL_CONFIG.mode, MODEL_CONFIG.description), 'a') as f:
				# 	w = csv.writer(f)
				# 	w.writerow(datapoints)
			# Update model epoch number and accuracy
			model.training_epoch += 1

			# Check if current validation accuracy exceeds the best accuracy
			if model.best_accuracy < val_acc:
				model.best_accuracy = val_acc
				model.save_to_checkpoint(MODEL_CONFIG.checkpoint_path, is_best=True)

		
		# Save traing loss into csv, epoch, mean_loss
		with open('{0}/{1}_{2}_train_loss.csv'.format(config.CSV, config.args.experiment, config.time.time()), 'a') as f:
			w = csv.writer(f, lineterminator='\n')
			w.writerows(lossdatapoints)
		# Save training and valid accuracy into csv, epoch, train_acc, val_acc
		with open('{0}/{1}_{2}_accuracy.csv'.format(config.CSV, config.args.experiment, config.time.time()), 'a') as f:
			w = csv.writer(f, lineterminator='\n')
			w.writerows(accuracy_datapoints)

		# Save the final model to a checkpoint.
		model.save_to_checkpoint(MODEL_CONFIG.checkpoint_path)

	# Run the model on the test set, using a new test dataloader.
	test_acc = train_utils.validate_model(model=parallel_model,
											dataloader=test_dataloader,
											loss_fn=loss_fn,
											is_lstm=MODEL_CONFIG.is_lstm,
											use_cuda=MODEL_CONFIG.use_cuda)
	logging.info('Test Acc: {:.2f}%.'.format(test_acc))

	# TODO Save (and maybe visualize or analyze?) the results.
	# Use:
	# 	- config.TRAIN_DIR for aggregate training/validation results
	# 	- config.TEST_DIR for aggregate testing results
	# 	- config.MODEL_DIR for general model information

if __name__ == '__main__':
	main()
