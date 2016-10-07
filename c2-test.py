#!/bin/ipython
import numpy as np
import cv2
import pyNN.nest as sim
import pathlib as plb
import time
import pickle
import argparse as ap
import sys
from sklearn import svm, metrics

import common as cm
import network as nw
import visualization as vis

parser = ap.ArgumentParser('./c1-spikes-from-file-test.py --')
parser.add_argument('--logfile', type=str,
                    help='File to output classification results')
parser.add_argument('--training-c1-dumpfile', type=str, required=True,
                    help='The output file to contain the C1 spiketrains for\
                         training')
parser.add_argument('--validation-c1-dumpfile', type=str, required=True,
                    help='The output file to contain the C1 spiketrains for\
                         validation')
parser.add_argument('--training-image-count', type=int, required=True,
                    help='The number of iterations for the images from the\
                         training dataset')
parser.add_argument('--validation-image-count', type=int, required=True,
                    help='The number of iterations for the images from the\
                         validation dataset')
parser.add_argument('--training-labels', type=str, required=True,
                    help='Text file which contains the labels of the training\
                          dataset')
parser.add_argument('--validation-labels', type=str, required=True,
                    help='Text file which contains the labels of the validation\
                          dataset')
parser.add_argument('--sim-time', default=50, type=float, metavar='50',
                     help='Simulation time')
parser.add_argument('--threads', default=1, type=int)
parser.add_argument('--weights-from', type=str, required=True,
                    help='Dumpfile of the S2 weight array')
args = parser.parse_args()

sim.setup(threads=args.threads, min_delay=.1)

layer_collection = {}

print('Create C1 layers')
t1 = time.clock()
training_ddict = pickle.load(open(args.training_c1_dumpfile, 'rb'))
validation_ddict = pickle.load(open(args.validation_c1_dumpfile, 'rb'))
layer_collection['C1'] = {}
for size, layers_as_dicts in training_ddict.items():
    layer_list = []
    for layer_as_dict in layers_as_dicts:
        n, m = layer_as_dict['shape']
        new_layer = nw.Layer(sim.Population(n * m,
                        sim.SpikeSourceArray(),
                        label=layer_as_dict['label']), (n, m))
        layer_list.append(new_layer)
    layer_collection['C1'][size] = layer_list
print('C1 creation took {} s'.format(time.clock() - t1))

print('Creating S2 layers and reading the epoch weights')
epoch_weights_list = pickle.load(open(args.weights_from, 'rb'))
epoch = epoch_weights_list[-1][0]
weights_dict_list = epoch_weights_list[-1][1]
f_s = int(np.sqrt(list(weights_dict_list[0].values())[0].shape[0]))
s2_prototype_cells = len(weights_dict_list)
layer_collection['S2'] = nw.create_S2_layers(layer_collection['C1'], f_s,
                                             s2_prototype_cells, stdp=False)

print('Creating C2 layers')
t1 = time.clock()
layer_collection['C2'] = nw.create_C2_layers(layer_collection['S2'],
                                             s2_prototype_cells)
print('C2 creation took {} s'.format(time.clock() - t1))

for pop in layer_collection['C2']:
    pop.record('spikes')

def set_c1_spiketrains(ddict):
    for size, layers_as_dicts in ddict.items():
        for layer_as_dict in layers_as_dicts:
            spiketrains = layer_as_dict['segment'].spiketrains
            dimensionless_sts = [[s for s in st] for st in spiketrains]
            the_layer_iter = filter(lambda layer: layer.population.label\
                            == layer_as_dict['label'], layer_collection['C1'][size])
            the_layer_iter.__next__().population.set(spike_times=dimensionless_sts)

training_labels = open(args.training_labels, 'r').read().splitlines()
validation_labels = open(args.validation_labels, 'r').read().splitlines()

def extract_data_samples(image_count):
    samples = []
    print('========= Start simulation =========')
    for i in range(image_count):
        print('Simulating for training image number', i)
        sim.run(args.sim_time)
        spikes =\
            [list(layer_collection['C2'][prot].get_spike_counts().values())[0]\
                for prot in range(s2_prototype_cells)]
        samples.append(spikes)
        for prot in range(s2_prototype_cells):
            layer_collection['C2'][prot].get_data(clear=True)
    print('========= Stop  simulation =========')
    return samples

logfile = sys.stdout
if args.logfile != None:
    logfile = open(args.logfile, 'w')

for epoch, weights_dict_list in epoch_weights_list:
    # Set the S2 weights to those from the file
    print('Setting S2 weights to epoch', epoch)
    for prototype in range(s2_prototype_cells):
        nw.set_s2_weights(layer_collection['S2'], prototype,
                          weights_dict_list=weights_dict_list)

    training_samples = []
    validation_samples = []

    print('Setting C1 spike trains to the training dataset')
    set_c1_spiketrains(training_ddict)
    print('>>>>>>>>> Extracting data samples for fitting <<<<<<<<<')
    training_samples = extract_data_samples(args.training_image_count)

    print('Setting C1 spike trains to the validation dataset')
    set_c1_spiketrains(validation_ddict)
    print('>>>>>>>>> Extracting data samples for validation <<<<<<<<<')
    validation_samples = extract_data_samples(args.validation_image_count)

    print('Fitting SVM model onto the training samples')

    clf = svm.SVC(kernel='linear')
    clf.fit(training_samples, training_labels)

    print('Predicting the categories of the validation samples')
    predicted_labels = clf.predict(validation_samples)
    print('============================================================',
          file=logfile)
    print('Epoch', epoch, file=logfile)
    print(metrics.classification_report(validation_labels, predicted_labels),
          file=logfile)
    print(metrics.confusion_matrix(validation_labels, predicted_labels),
          file=logfile)

sim.end()
