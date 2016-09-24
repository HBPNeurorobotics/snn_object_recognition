#!/bin/ipython
import numpy as np
import cv2
import sys
import pyNN.nest as sim
import pathlib as plb
import time
import pickle
import argparse as ap
import signal

import common as cm
import network as nw
import visualization as vis
import time

try:
    from mpi4py import MPI
except ImportError:
    raise Exception("Trying to gather data without MPI installed. If you are\
    not running a distributed simulation, this is a bug in PyNN.")

parser = ap.ArgumentParser('./c1-spikes-from-file-test.py --')
parser.add_argument('--c1-dumpfile', type=str, required=True,
                    help='The output file to contain the C1 spiketrains')
parser.add_argument('--dataset-label', type=str, required=True,
                    help='The name of the dataset which was used for\
                    training')
parser.add_argument('--feature-size', type=int, default=3,
                     help='The size of the features to be learnt')
parser.add_argument('--s2-prototype-cells', type=int, default=3,
                    help='The number of S2 features to compute')
parser.add_argument('--image-count', type=int, required=True,
                    help='The number of images to read from the training\
                    directory')
parser.add_argument('--plot-c1-spikes', action='store_true',
                    help='Plot the spike trains of the C1 layers')
parser.add_argument('--plot-s2-spikes', action='store_true',
                    help='Plot the spike trains of the S2 layers')
parser.add_argument('--refrac-s2', type=float, default=.1, metavar='.1',
                    help='The refractory period of neurons in the S2 layer in\
                    ms')
parser.add_argument('--sim-time', default=50, type=float, metavar='50',
                     help='Simulation time')
parser.add_argument('--threads', default=1, type=int)
parser.add_argument('--weights-from', type=str,
                    help='File containing the initial weights and initial image')
parser.add_argument('--weights-to', type=str, required=True,
                    help='File to dump the weights to')
args = parser.parse_args()

def handler(signum, frame):
    print('Caught signal', signum)
    print('Dumping the weights to file')
    pickle.dump((current_weights, i), out_dumpfile)
    sys.exit(2)

signal.signal(signal.SIGFPE, handler)
signal.signal(signal.SIGABRT, handler)
signal.signal(signal.SIGBUS, handler)
signal.signal(signal.SIGILL, handler)
signal.signal(signal.SIGSEGV, handler)

MPI_ROOT = 0

def is_root():
    return MPI.COMM_WORLD.rank == MPI_ROOT 

sim.setup(threads=args.threads)

layer_collection = {}

# Read the gabor features for reconstruction
feature_imgs_dict = {} # feature string -> image
for filepath in plb.Path('features_gabor').iterdir():
    feature_imgs_dict[filepath.stem] = cv2.imread(filepath.as_posix(),
                                                  cv2.CV_8UC1)

dataset_label = '{}_fs{}_{}imgs_{}ms_scales'.format(args.dataset_label,
                                        args.feature_size, args.image_count,
                                        int(args.sim_time))

if is_root():
    print('Create C1 layers')
    t1 = time.clock()
dumpfile = open(args.c1_dumpfile, 'rb')
ddict = pickle.load(dumpfile)
layer_collection['C1'] = {}
for size, layers_as_dicts in ddict.items():
    dataset_label += '_{}'.format(str(size))
    layer_list = []
    for layer_as_dict in layers_as_dicts:
        n, m = layer_as_dict['shape']
        spiketrains = layer_as_dict['segment'].spiketrains
        dimensionless_sts = [[s for s in st] for st in spiketrains]
        new_layer = nw.Layer(sim.Population(n * m,
                        sim.SpikeSourceArray(spike_times=dimensionless_sts),
                        label=layer_as_dict['label']), (n, m))
        layer_list.append(new_layer)
    layer_collection['C1'][size] = layer_list
if is_root():
    print('C1 creation took {} s'.format(time.clock() - t1))

if is_root():
    print('Creating S2 layers')
    t1 = time.clock()
layer_collection['S2'] = nw.create_S2_layers(layer_collection['C1'], args)
if is_root():
    print('S2 creation took {} s'.format(time.clock() - t1))

for layers in layer_collection['C1'].values():
    for layer in layers:
        layer.population.record('spikes')
for layer_list in layer_collection['S2'].values():
    for layer in layer_list:
        layer.population.record(['spikes', 'v'])

if is_root():
    reconstructions_dir_dataset_path = plb.Path('S2_reconstructions/' + dataset_label)
    for i in range(args.s2_prototype_cells):
        reconstructions_dir_path = reconstructions_dir_dataset_path / str(i)
        if not reconstructions_dir_path.exists():
            reconstructions_dir_path.mkdir(parents=True)
    c1_plots_dir_path = plb.Path('plots/C1/' + dataset_label)
    if not c1_plots_dir_path.exists():
        c1_plots_dir_path.mkdir(parents=True)
    s2_plots_dataset_dir = plb.Path('plots/S2/' + dataset_label)
    for i in range(args.s2_prototype_cells):
        s2_plots_dir_path = s2_plots_dataset_dir / str(i)
        if not s2_plots_dir_path.exists():
            s2_plots_dir_path.mkdir(parents=True)

out_dumpfile = open(args.weights_to, 'wb')

if is_root():
    print('========= Start simulation =========')
    start_time = time.clock()
for i in range(args.image_count):
    if is_root():
        print('Simulating for image number', i)
    sim.run(args.sim_time)
    if is_root():
        if args.plot_c1_spikes:
            vis.plot_C1_spikes(layer_collection['C1'],
                               '{}_image_{}'.format(dataset_label, i),
                               out_dir_name=c1_plots_dir_path.as_posix())
        if args.plot_s2_spikes:
            vis.plot_S2_spikes(layer_collection['S2'],
                           '{}_image_{}'.format(dataset_label, i),
                           args.s2_prototype_cells,
                           out_dir_name=s2_plots_dataset_dir.as_posix())
    if is_root():
        if (i + 1) % 10 == 0:
            current_weights = nw.get_current_weights(layer_collection['S2'],
                                                     args.s2_prototype_cells)
            for j in range(args.s2_prototype_cells):
                cv2.imwrite('{}/{}_prototype{}_{}_images.png'.format(\
                        (reconstructions_dir_dataset_path / str(j)).as_posix(),
                         dataset_label, j, i + 1),
                    vis.reconstruct_S2_features(current_weights[j],
                                                feature_imgs_dict,
                                                args.feature_size))
if is_root():
    end_time = time.clock()
    print('========= Stop  simulation =========')
    print('Simulation took', end_time - start_time, 's')
    # Reconstruct the last image
    for j in range(args.s2_prototype_cells):
        cv2.imwrite('{}/{}_prototype{}_{}_images.png'.format(\
                            (reconstructions_dir_dataset_path / str(j)).as_posix(),
                            dataset_label, j, i + 1),
                vis.reconstruct_S2_features(current_weights[j],
                                            feature_imgs_dict,
                                            args.feature_size))
    print('Dumping trained weights to file', args.weights_to)
    pickle.dump(current_weights, out_dumpfile)

sim.end()
