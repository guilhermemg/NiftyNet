# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

from six.moves import range

from niftynet.layer import layer_util
from niftynet.layer.activation import ActiLayer
from niftynet.layer.base_layer import TrainableLayer
from niftynet.layer.bn import BNLayer
from niftynet.layer.convolution import ConvLayer, ConvolutionalLayer
from niftynet.layer.convolution import LearnedCategoricalGroupConvolutionalLayer
from niftynet.layer.annealing import gumbel_softmax_decay
from niftynet.layer.dilatedcontext import DilatedTensor
from niftynet.layer.elementwise import ElementwiseLayer
from niftynet.network.base_net import BaseNet

import tensorflow as tf

class HighRes3DNet(BaseNet):

    ## only performing clustering at layer 1 and after residual blocks

    def __init__(self,
                 num_classes,
                 w_initializer=None,
                 w_regularizer=None,
                 b_initializer=None,
                 b_regularizer=None,
                 acti_func='prelu',
                 name='HighRes3DNet'):

        super(HighRes3DNet, self).__init__(
            num_classes=num_classes,
            w_initializer=w_initializer,
            w_regularizer=w_regularizer,
            b_initializer=b_initializer,
            b_regularizer=b_regularizer,
            acti_func=acti_func,
            name=name)

        self.layers = [
            {'name': 'conv_0', 'n_features': 16, 'kernel_size': 3},
            {'name': 'res_1', 'n_features': 16, 'kernels': (3, 3), 'repeat': 3},
            {'name': 'res_2', 'n_features': 32, 'kernels': (3, 3), 'repeat': 3},
            {'name': 'res_3', 'n_features': 64, 'kernels': (3, 3), 'repeat': 3},
            {'name': 'conv_1', 'n_features': 80, 'kernel_size': 1},
            {'name': 'conv_2', 'n_features': num_classes, 'kernel_size': 1}]

    def layer_op(self, images, is_training=True, layer_id=-1, **unused_kwargs):
        assert layer_util.check_spatial_dims(
            images, lambda x: x % 8 == 0)
        # go through self.layers, create an instance of each layer
        # and plugin data
        layer_instances = []

        ### first convolution layer
        params = self.layers[0]
        conv_layer = LearnedCategoricalGroupConvolutionalLayer(
            n_output_chns=params['n_features'],
            kernel_size=params['kernel_size'],
            categorical=True,
            use_hardcat=unused_kwargs['use_hardcat'],
            learn_cat=unused_kwargs['learn_cat'],
            p_init=self.p_init,
            init_cat=unused_kwargs['init_cat'],
            constant_grouping=unused_kwargs['constant_grouping'],
            group_connection=unused_kwargs['group_connection'],
            acti_func=self.acti_func,
            w_initializer=self.initializers['w'],
            w_regularizer=self.regularizers['w'],
            name=params['name'])
        grouped_flow, learned_mask, d_p = conv_layer(images, is_training)

        # Output of grouped_flow is a list: [task_1, shared, task_2]
        # where task_1 = task_1 + shared
        #       task_2 = task_2 + shared

        ### resblocks, all kernels dilated by 1 (normal convolution) on sparse tensors
        params = self.layers[1]
        # iterate over clustered activation maps
        clustered_res_block = []
        for clustered_tensor in grouped_flow:
            with DilatedTensor(clustered_tensor, dilation_factor=1) as dilated:
                for j in range(params['repeat']):
                    res_block = HighResBlock(
                        params['n_features'],
                        params['kernels'],
                        acti_func=self.acti_func,
                        w_initializer=self.initializers['w'],
                        w_regularizer=self.regularizers['w'],
                        name='%s_%d' % (params['name'], j))
                    dilated.tensor = res_block(dilated.tensor, is_training)
                    layer_instances.append((res_block, dilated.tensor))
            clustered_res_block.append(dilated.tensor)

        # merging of task-specific and task-invariant features
        task_1 = clustered_res_block[0] + clustered_res_block[1]
        task_2 = clustered_res_block[2] + clustered_res_block[1]
        shared = clustered_res_block[1]
        output_res_blocks = [task_1, shared, task_2]

        ### resblocks, all kernels dilated by 2
        params = self.layers[2]
        clustered_res_block = []
        for clustered_tensor in output_res_blocks:
            with DilatedTensor(clustered_tensor, dilation_factor=2) as dilated:
                for j in range(params['repeat']):
                    res_block = HighResBlock(
                        params['n_features'],
                        params['kernels'],
                        acti_func=self.acti_func,
                        w_initializer=self.initializers['w'],
                        w_regularizer=self.regularizers['w'],
                        name='%s_%d' % (params['name'], j))
                    dilated.tensor = res_block(dilated.tensor, is_training)
                    layer_instances.append((res_block, dilated.tensor))
            clustered_res_block.append(dilated.tensor)

        # merging of task-specific and task-invariant features
        task_1 = clustered_res_block[0] + clustered_res_block[1]
        task_2 = clustered_res_block[2] + clustered_res_block[1]
        shared = clustered_res_block[1]
        output_res_blocks = [task_1, shared, task_2]

        ### resblocks, all kernels dilated by 4
        params = self.layers[3]
        clustered_res_block = []
        for clustered_tensor in output_res_blocks:
            with DilatedTensor(clustered_tensor, dilation_factor=4) as dilated:
                for j in range(params['repeat']):
                    res_block = HighResBlock(
                        params['n_features'],
                        params['kernels'],
                        acti_func=self.acti_func,
                        w_initializer=self.initializers['w'],
                        w_regularizer=self.regularizers['w'],
                        name='%s_%d' % (params['name'], j))
                    dilated.tensor = res_block(dilated.tensor, is_training)
                    layer_instances.append((res_block, dilated.tensor))
            clustered_res_block.append(dilated.tensor)

        # merging of task-specific and task-invariant features
        task_1 = clustered_res_block[0] + clustered_res_block[1]
        task_2 = clustered_res_block[2] + clustered_res_block[1]
        shared = clustered_res_block[1]
        output_res_blocks = [task_1, shared, task_2]

        ### 1x1x1 convolution layer
        params = self.layers[4]
        conv_layer = LearnedCategoricalGroupConvolutionalLayer(
            n_output_chns=params['n_features'],
            kernel_size=params['kernel_size'],
            categorical=True,
            use_hardcat=unused_kwargs['use_hardcat'],
            learn_cat=unused_kwargs['learn_cat'],
            p_init=self.p_init,
            init_cat=unused_kwargs['init_cat'],
            constant_grouping=unused_kwargs['constant_grouping'],
            group_connection=unused_kwargs['group_connection'],
            acti_func=self.acti_func,
            w_initializer=self.initializers['w'],
            w_regularizer=self.regularizers['w'],
            name=params['name'])
        grouped_flow, learned_mask, d_p = conv_layer(output_res_blocks, is_training)
        layer_instances.append((conv_layer, grouped_flow))

        ### 1x1x1 convolution layer
        params = self.layers[5]
        conv_layer_task_1 = LearnedCategoricalGroupConvolutionalLayer(
                n_output_chns=params['n_features'],
                kernel_size=params['kernel_size'],
                categorical=True,
                use_hardcat=unused_kwargs['use_hardcat'],
                learn_cat=unused_kwargs['learn_cat'],
                p_init=self.p_init,
                init_cat=unused_kwargs['init_cat'],
                constant_grouping=unused_kwargs['constant_grouping'],
                group_connection=unused_kwargs['group_connection'],
                acti_func=self.acti_func,
                w_initializer=self.initializers['w'],
                w_regularizer=self.regularizers['w'],
                name=params['name'])
        task_1_output, learned_mask, d_p = conv_layer_task_1(grouped_flow[0], is_training)
        layer_instances.append((conv_layer, task_1_output))

        conv_layer_task_2 = LearnedCategoricalGroupConvolutionalLayer(
                n_output_chns=params['n_features'],
                kernel_size=params['kernel_size'],
                categorical=True,
                use_hardcat=unused_kwargs['use_hardcat'],
                learn_cat=unused_kwargs['learn_cat'],
                p_init=self.p_init,
                init_cat=unused_kwargs['init_cat'],
                constant_grouping=unused_kwargs['constant_grouping'],
                group_connection=unused_kwargs['group_connection'],
                acti_func=self.acti_func,
                w_initializer=self.initializers['w'],
                w_regularizer=self.regularizers['w'],
                name=params['name'])
        task_2_output, learned_mask, d_p = conv_layer_task_2(grouped_flow[-1], is_training)
        layer_instances.append((conv_layer, task_2_output))

        # set training properties
        if is_training:
            self._print(layer_instances)
            return [task_1_output, task_2_output]
        return layer_instances[layer_id][1]

    def _print(self, list_of_layers):
        for (op, _) in list_of_layers:
            print(op)


class HighResBlock(TrainableLayer):
    """
    This class define a high-resolution block with residual connections
    kernels

        - specify kernel sizes of each convolutional layer
        - e.g.: kernels=(5, 5, 5) indicate three conv layers of kernel_size 5

    with_res

        - whether to add residual connections to bypass the conv layers
    """

    def __init__(self,
                 n_output_chns,
                 kernels=(3, 3),
                 acti_func='relu',
                 w_initializer=None,
                 w_regularizer=None,
                 with_res=True,
                 name='HighResBlock'):

        super(HighResBlock, self).__init__(name=name)

        self.n_output_chns = n_output_chns
        if hasattr(kernels, "__iter__"):  # a list of layer kernel_sizes
            self.kernels = kernels
        else:  # is a single number (indicating single layer)
            self.kernels = [kernels]
        self.acti_func = acti_func
        self.with_res = with_res

        self.initializers = {'w': w_initializer}
        self.regularizers = {'w': w_regularizer}

    def layer_op(self, input_tensor, is_training):
        output_tensor = input_tensor
        for (i, k) in enumerate(self.kernels):
            # create parameterised layers
            bn_op = BNLayer(regularizer=self.regularizers['w'],
                            name='bn_{}'.format(i))
            acti_op = ActiLayer(func=self.acti_func,
                                regularizer=self.regularizers['w'],
                                name='acti_{}'.format(i))
            conv_op = ConvLayer(n_output_chns=self.n_output_chns,
                                kernel_size=k,
                                stride=1,
                                w_initializer=self.initializers['w'],
                                w_regularizer=self.regularizers['w'],
                                name='conv_{}'.format(i))
            # connect layers
            output_tensor = bn_op(output_tensor, is_training)
            output_tensor = acti_op(output_tensor)
            output_tensor = conv_op(output_tensor)
        # make residual connections
        if self.with_res:
            output_tensor = ElementwiseLayer('SUM')(output_tensor, input_tensor)
        return output_tensor
