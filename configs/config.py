import argparse
import logging
import os
import time

from collections import namedtuple
from configobj import ConfigObj
from validate import Validator

import torch
import torchvision.models as models
import torchvision.transforms as transforms
import torchvision.models as models

from models.LSTM import EncodingLSTMClassifier
from models.ResNet import PretrainedResNetClassifier
from utils import data_loader

parser = argparse.ArgumentParser(description='Gesture classification task.')

parser.add_argument('--mode', type=str, default='train',
	help='Running mode: "train" or "test" or "pickle".')
parser.add_argument('--pickle_overwrite', action='store_true')
parser.add_argument('--pickle_dataset', type=str)
parser.add_argument('--pickle_label_start', type=int)
parser.add_argument('--pickle_label_end', type=int)

# For training, testing and pickling
parser.add_argument('--experiment', type=str,
	help='Name of the experiment: LSTM(RGB)-1.0, LSTM(RGBD)-1.0, RESNET18(RGB)-1.0, RESNET18(RGBD)-1.0')
parser.add_argument('--arch', type=str,
	help='Type of architecture for the model. Experiment should have set this by default')
parser.add_argument('--dataloader_type', type=str,
	help='Experiment should have set this by default. Change the dataloader_type only if you know what you are doing\n' +
	'Type of dataloaders: (RN18RGB-encoding+TRN18RGBD-encoding)-combination, RN18RGB-encoding, RN18RGBD-encoding, RGB-image, RGBD-image')
# General hyperparameters
parser.add_argument('--starting_from', type=int, default=1)
parser.add_argument('--use_full_dataset', action='store_true')
parser.add_argument('--shuffle', action='store_true')
parser.add_argument('--max_example_per_label', type=int)
parser.add_argument('--batch_size', type=int)
parser.add_argument('--epochs', type=int)
parser.add_argument('--learning_rate', type=float)
parser.add_argument('--weight_decay', type=float, default=0.)
parser.add_argument('--dropout', type=float, default=0.)
parser.add_argument('--optimizer', type=str, default='adam',
					help='Type of optimizer function: adam, sgd, adagrad, rmsprop')
parser.add_argument('--initializer', type=str, default='xavier',
					help='Type of initializer function: xavier, he')
parser.add_argument('--loss', type=str, default='cross-entropy',
					help='Type of loss function: cross-entropy, nll')
# TODO Add in schedulers to optimize the learning: torch.optim.lr_scheduler
# ResNet specific arguments
parser.add_argument('--pretrained', action='store_true')
parser.add_argument('--resnet_num_layers', type=int,
					help='Number of layer of a pretrained resnet: 18, 34, 50, 101, 152')
parser.add_argument('--freeze', action='store_true')
# LSTM specific arguments
parser.add_argument('--lstm_hidden_size', type=int)
parser.add_argument('--lstm_hidden_fc_size', type=int)
parser.add_argument('--lstm_num_layers', type=int, default=1)
parser.add_argument('--lstm_bias', action='store_true', default=False)
parser.add_argument('--lstm_batch_first', action='store_true', default=True)
parser.add_argument('--lstm_bidirectional', action='store_true', default=False)
parser.add_argument('--max_seq_len', type=int,
					help='Maximum temporal depth of video frames on which to train.')
# General arguments
parser.add_argument('--debug', action='store_true')
parser.add_argument('--verbose', action='store_true')
parser.add_argument('--use_cuda', action='store_true')
parser.add_argument('--save_every_epoch', action='store_true')
parser.add_argument('--validate_every', type=int, default=10,
					help='Number of interval epochs between which to validate the model.')
parser.add_argument('--num_workers', type=int, default=16,
					help='Number of separate processes with which to run the DataLoader. '
					'Set to a value, e.g. 4, when running on a VM with high compute.')
# Loading and saving of checkpoints
parser.add_argument('--load', action='store_true')
parser.add_argument('--num_sweeps', type=int, default=0)
parser.add_argument('--checkpoint_to_load', type=str,
					help='Provide the name of the checkpoint to load')

args = parser.parse_args()

# Perform check to ensure the name of experiment is set
if (args.mode == 'train' or args.mode == 'test') and not args.experiment:
	raise ValueError('Name of experiment is not specified. Please state a experiment to proceed.')

class ConfigObjFromDict(object):
	"""Handy class for creating an object updated as a dict but accessed as an obj."""
	def __init__(self, **entries):
		self.__dict__.update(entries)

	def __setattr__(self, name, value):
		self.__dict__[name] = value

	def __getattr__(self, name):
		return self.__dict__.get(name, None)

	def __str__(self):
		return ' '.join(['{0}: {1}\n'.format(k, v) for k, v in self.__dict__.items()])

	def __copy__(self):
		return ConfigObjFromDict(**self.__dict__)

	def to_dict(self):
		return self.__dict__


###################
# Major Directories
###################

def mkdir(path):
	"""Recursive mkdir, without overwriting existing directories."""
	parent, child = os.path.split(path)
	if not os.path.exists(parent):
		mkdir(parent)

	if not os.path.exists(path):
		os.mkdir(path)

# Major directories
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))	# /home/shared
PROJECT_DIR = os.path.join(ROOT_DIR, 'cs231n-sl-classification')		# cs231n-sl-classification
DATASET_DIR = os.path.join(ROOT_DIR, 'dataset')

CONFIGS_DIR = os.path.join(PROJECT_DIR, 'configs')

TEST_DATA_DIR = os.path.join(DATASET_DIR, 'test')
TRAIN_DATA_DIR = os.path.join(DATASET_DIR, 'train')
VALID_DATA_DIR = os.path.join(DATASET_DIR, 'valid')

################
# Configurations
################

# Read the flag and configuration file values and validate them to cast them to their
# specified data types.
exp_config = ConfigObj(os.path.join(CONFIGS_DIR, '{0}.ini'.format(args.experiment)),
	configspec=os.path.join(CONFIGS_DIR, 'configspec.ini'))
exp_config.validate(Validator())

# Merge the flag and configuration file values, overwriting exp_config values with flag values.
for k, v in vars(args).items():
	if v:
		exp_config[k] = v
MODEL_CONFIG = ConfigObjFromDict(**exp_config)


########################
# Dataset Configuration
########################

# TODO: Add a configuration for the sampling scheme.

########
# Models
########

model_dict = {
	'EncodingLSTMClassifier': EncodingLSTMClassifier,
	'PretrainedResNetClassifier': PretrainedResNetClassifier
}

MODEL_CONFIG.model = model_dict[MODEL_CONFIG.arch]
MODEL_CONFIG.is_lstm = (MODEL_CONFIG.arch == 'EncodingLSTMClassifier')

# Whether to use the full dataset.
TOTAL_NUMBER_OF_LABELS = 249
if MODEL_CONFIG.use_full_dataset:
	MODEL_CONFIG.gesture_labels = range(MODEL_CONFIG.starting_from, TOTAL_NUMBER_OF_LABELS + 1)

########################
# Model Hyperparameters
########################
# https://pytorch.org/docs/master/torchvision/models.html
# 
# All pre-trained models expect input images normalized in the same way,
# i.e. mini-batches of 3-channel RGB images of shape (3 x H x W), where
# H and W are expected to be at least 224. The images have to be loaded
# in to a range of [0, 1] and then normalized using

normalize = None

if MODEL_CONFIG.dataloader_type == 'RGB-image':
	normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
								 std=[0.229, 0.224, 0.225])
elif MODEL_CONFIG.dataloader_type == 'RGBD-image':
	normalize = transforms.Normalize(mean=[0.1337, 0.1337, 0.1337],
								 std=[0.2541, 0.2547, 0.2539])
elif MODEL_CONFIG.dataloader_type == 'OFRGB-image':
	normalize = transforms.Normalize(mean=[-0.2686, -0.2686, -0.1343],
								std=[0.0655,  0.5214,  0.4264])
elif MODEL_CONFIG.dataloader_type == 'OFRGBD-image':
	normalize = transforms.Normalize(mean=1.00e-2*[-6.7142, -6.7142, -6.7142],
								std=[0.2501,  0.3951,  0.3619])

MODEL_CONFIG.transform = transforms.Compose([
			transforms.ToPILImage(),
			transforms.CenterCrop(224),
			transforms.ToTensor(),
			normalize
		])


optimizer_dict = {
	'adam': torch.optim.Adam,
	'sgd': torch.optim.SGD,
	'adagrad': torch.optim.Adagrad,
	'rmsprop': torch.optim.RMSprop
}

initializer_dict = {
	'xavier': torch.nn.init.xavier_normal_,
	'he': torch.nn.init.kaiming_normal_
}

loss_dict = {
	'cross-entropy': torch.nn.CrossEntropyLoss(),
	'nll': torch.nn.NLLLoss()
}

MODEL_CONFIG.optimizer_fn = optimizer_dict[MODEL_CONFIG.optimizer]
MODEL_CONFIG.initializer_fn = initializer_dict[MODEL_CONFIG.initializer]
MODEL_CONFIG.loss_fn = loss_dict[MODEL_CONFIG.loss]

################
# Miscellaneous
################

MODEL_CONFIG.seed = 1

########################
# Experiment Directories
########################

# Creates all prerequisite directories for our model.
EXPERIMENTS_DIR = os.path.join(PROJECT_DIR, 'experiments', MODEL_CONFIG.experiment)
MODEL_DIR = os.path.join(EXPERIMENTS_DIR, 'checkpoints')	# cs231n-sl-classification/experiments/{experiment-name}/checkpoints/
LOG_DIR = os.path.join(EXPERIMENTS_DIR, 'logs')				# cs231n-sl-classification/experiments/{experiment-name}/logs/
METRICS = os.path.join(EXPERIMENTS_DIR, 'metrics')
PLOTS = os.path.join(EXPERIMENTS_DIR, 'plots')

mkdir(MODEL_DIR)
mkdir(LOG_DIR)
mkdir(METRICS)
mkdir(PLOTS)

#################
# Checkpoint Path
#################

# File to seralize our PyTorch model to. Existing checkpoint will be overwritten.
# https://pytorch.org/docs/stable/torch.html#torch.save
MODEL_CONFIG.checkpoint_path = MODEL_DIR

##########
# Logging
##########
# File in which to log output.
# Just import logging, config in a library to log to the same location.
curr_time = time.time()
info_logfile = os.path.join(LOG_DIR, '{0}-{1}.INFO'.format(args.experiment, curr_time)) 
debug_logfile = os.path.join(LOG_DIR, '{0}-{1}.DEBUG'.format(args.experiment, curr_time))

try:
	info_f = open(info_logfile, 'r')
	debug_f = open(debug_logfile, 'r')
except IOError:
	info_f = open(info_logfile, 'w')
	debug_f = open(debug_logfile, 'w')
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] %(message)s')

# Set up a streaming logger.
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)

fh_info = logging.FileHandler(info_logfile)
fh_info.setLevel(logging.INFO)
fh_info.setFormatter(formatter)

fh_debug = logging.FileHandler(debug_logfile)
fh_debug.setLevel(logging.DEBUG)
fh_debug.setFormatter(formatter)

logger.addHandler(ch)
logger.addHandler(fh_info)
logger.addHandler(fh_debug)
