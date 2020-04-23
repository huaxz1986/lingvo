# Lint as: python3
# Copyright 2018 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tests for lingvo.core.conv_layers_with_time_padding."""


import lingvo.compat as tf
from lingvo.core import conv_layers_with_time_padding
from lingvo.core import py_utils
from lingvo.core import test_utils
from lingvo.core import tshape
import numpy as np
from six.moves import zip


class ConvLayerTest(test_utils.TestCase):
  """Tests conv layers.

  Note that there are multiple subclasses of BaseConv2DLayer and most cases
  are tested via the concrete Conv2DLayer. Other tests are done against
  other subclasses to cover key differences.
  """

  def testConv2DLayerConstruction(self):
    with self.session(use_gpu=True):
      tf.set_random_seed(398847392)
      np.random.seed(12345)
      params = conv_layers_with_time_padding.Conv2DLayerWithPadding.Params()
      params.name = 'conv'
      params.filter_shape = [3, 3, 3, 32]
      params.filter_stride = [2, 2]
      params.params_init = py_utils.WeightInit.Gaussian(0.1)
      _ = params.Instantiate()
      conv_vars = tf.get_collection('Conv2DLayerWithPadding_vars')
      conv_var_names = [x.name for x in conv_vars]
      expected_var_names = ['conv/w/var:0']
      self.assertEqual(expected_var_names, conv_var_names)

  def testConv2DLayerWithPaddingOutputChannels(self):
    with self.session():
      params = conv_layers_with_time_padding.Conv2DLayerWithPadding.Params()
      params.name = 'conv'
      params.filter_shape = [3, 3, 3, 32]
      actual_output_channels = params.cls.OutputChannels(params)
      self.assertEqual(32, actual_output_channels)

  def testConv2DLayerOutShape(self):
    with self.session(use_gpu=True):
      tf.set_random_seed(398847392)
      np.random.seed(12345)
      params = conv_layers_with_time_padding.Conv2DLayerWithPadding.Params()
      params.name = 'conv'
      params.filter_shape = [3, 3, 3, 32]
      params.filter_stride = [2, 2]
      params.params_init = py_utils.WeightInit.Gaussian(0.1)
      conv_layer = params.Instantiate()
      in_shape = [None, None, 10, 3]
      out_shape = conv_layer.OutShape(in_shape)
      self.assertEqual(out_shape, [None, None, 5, 32])
      in_shape = [None, 20, 10, 3]
      out_shape = conv_layer.OutShape(in_shape)
      self.assertEqual(out_shape, [None, 10, 5, 32])

  def testConv2DLayerWithPaddingFProp(self):
    with self.session(use_gpu=True) as sess:
      tf.set_random_seed(398847392)
      np.random.seed(12345)

      params = conv_layers_with_time_padding.Conv2DLayerWithPadding.Params()
      params.weight_norm = True
      params.filter_stride = [2, 2]
      params.name = 'conv'
      params.filter_shape = [3, 3, 3, 2]
      params.params_init = py_utils.WeightInit.Gaussian(0.1)
      conv_layer = params.Instantiate()
      in_padding1 = tf.zeros([2, 4], dtype=tf.float32)
      inputs1 = tf.constant(
          np.random.normal(0.1, 0.5, [2, 4, 4, 3]), dtype=tf.float32)
      output, _ = conv_layer.FPropDefaultTheta(inputs1, in_padding1)
      out_sum = tf.reduce_sum(output)
      out_sum_squared = tf.reduce_sum(output * output)
      tf.global_variables_initializer().run()
      v1, v2 = sess.run([out_sum, out_sum_squared])
      tf.logging.info('actual = %f, %f', v1, v2)
      self.assertAllClose([-0.293671, 4.198602], [v1, v2])

  def testCausalConv2DLayerWithPaddingFProp(self):
    with self.session(use_gpu=True) as sess:
      tf.set_random_seed(398847392)
      np.random.seed(12345)

      params = (
          conv_layers_with_time_padding.CausalConv2DLayerWithPadding.Params())
      params.weight_norm = True
      params.filter_stride = [2, 2]
      params.name = 'conv'
      params.filter_shape = [2, 1, 3, 2]
      params.params_init = py_utils.WeightInit.Gaussian(0.1)
      conv_layer = params.Instantiate()
      in_padding1 = tf.zeros([2, 4], dtype=tf.float32)
      inputs1 = tf.constant(
          np.random.normal(0.1, 0.5, [2, 4, 4, 3]), dtype=tf.float32)
      output, _ = conv_layer.FPropDefaultTheta(inputs1, in_padding1)
      tf.global_variables_initializer().run()
      out_sum = tf.reduce_sum(output)
      out_sum_squared = tf.reduce_sum(output * output)
      tf.global_variables_initializer().run()
      v1, v2 = sess.run([out_sum, out_sum_squared])
      tf.logging.info('actual = %f, %f', v1, v2)
      self.assertAllClose([-3.584711, 3.324082], [v1, v2])

  def testDepthwiseConv2DLayerOutputChannels(self):
    with self.session():
      params = conv_layers_with_time_padding.DepthwiseConv2DLayer.Params()
      params.name = 'conv'
      params.filter_shape = [3, 3, 3, 2]
      actual_output_channels = params.cls.OutputChannels(params)
      self.assertEqual(6, actual_output_channels)

  def testDepthwiseConv2DLayerFProp(self):
    with self.session(use_gpu=True) as sess:
      tf.set_random_seed(398847392)
      np.random.seed(12345)

      params = conv_layers_with_time_padding.DepthwiseConv2DLayer.Params()
      params.weight_norm = True
      params.filter_stride = [2, 2]
      params.name = 'conv'
      params.filter_shape = [3, 3, 3, 2]
      params.params_init = py_utils.WeightInit.Gaussian(0.1)
      conv_layer = params.Instantiate()
      in_padding1 = tf.zeros([2, 4], dtype=tf.float32)
      inputs1 = tf.constant(
          np.random.normal(0.1, 0.5, [2, 4, 4, 3]), dtype=tf.float32)
      output, _ = conv_layer.FPropDefaultTheta(inputs1, in_padding1)
      tf.global_variables_initializer().run()
      out_sum = tf.reduce_sum(output)
      out_sum_squared = tf.reduce_sum(output * output)
      tf.global_variables_initializer().run()
      v1, v2 = sess.run([out_sum, out_sum_squared])
      tf.logging.info('actual = %f, %f', v1, v2)
      self.assertAllClose([-1.455162, 6.813269], [v1, v2])

  def testCausalDepthwiseConv2DLayer(self):
    with self.session(use_gpu=True) as sess:
      tf.set_random_seed(398847392)
      np.random.seed(12345)

      params = conv_layers_with_time_padding.CausalDepthwiseConv2DLayer.Params()
      params.weight_norm = True
      params.filter_stride = [2, 2]
      params.name = 'conv'
      params.filter_shape = [2, 1, 3, 2]
      params.params_init = py_utils.WeightInit.Gaussian(0.1)

      conv_layer = params.Instantiate()
      in_padding1 = tf.zeros([2, 4], dtype=tf.float32)
      inputs1 = tf.constant(
          np.random.normal(0.1, 0.5, [2, 4, 4, 3]), dtype=tf.float32)
      output, _ = conv_layer.FPropDefaultTheta(inputs1, in_padding1)
      tf.global_variables_initializer().run()
      tf.global_variables_initializer().run()
      out_sum = tf.reduce_sum(output)
      out_sum_squared = tf.reduce_sum(output * output)
      tf.global_variables_initializer().run()
      v1, v2 = sess.run([out_sum, out_sum_squared])
      tf.logging.info('actual = %f, %f', v1, v2)
      self.assertAllClose([-2.031689, 7.911201], [v1, v2])

  def testActivationLayer(self):
    with self.session(use_gpu=True) as sess:
      p = conv_layers_with_time_padding.ActivationLayer.Params()
      p.name = 'act'
      l = p.Instantiate()
      inputs = tf.constant(
          np.random.normal(0.1, 0.5, [2, 4, 4, 3]), dtype=tf.float32)
      in_padding = tf.zeros([2, 4], dtype=tf.float32)
      out, out_padding = l.FProp(l.theta, inputs, in_padding)
      tf.global_variables_initializer().run()
      v1, v2 = sess.run([out, out_padding])
      print(v1, v2)

  def _testNormalizedDepthwiseConv2DHelper(self,
                                           is_causal=False,
                                           dropconnect_prob=0):
    if is_causal:
      conv_cls = (
          conv_layers_with_time_padding.CausalNormalizedDepthwiseConv2DLayer)
    else:
      conv_cls = conv_layers_with_time_padding.NormalizedDepthwiseConv2DLayer
    tf.set_random_seed(398847392)
    np.random.seed(12345)
    params = conv_cls.Params().Set(
        name='conv',
        weight_tiling_factor=2,
        filter_shape=[3, 1, 2, 1],
        dropconnect_prob=dropconnect_prob,
        deterministic_dropout=True)
    conv_layer = params.Instantiate()
    in_padding = tf.zeros([2, 4], dtype=tf.float32)
    inputs = tf.constant(
        np.random.normal(0.1, 0.5, [2, 4, 1, 4]), dtype=tf.float32)
    output, _ = conv_layer.FPropDefaultTheta(inputs, in_padding)
    return output

  def testNormalizedDepthwiseConv2DLayerOutputChannels(self):
    with self.session():
      params = (
          conv_layers_with_time_padding.NormalizedDepthwiseConv2DLayer.Params())
      params.name = 'conv'
      params.filter_shape = [3, 1, 2, 1]
      params.weight_tiling_factor = 2
      actual_output_channels = params.cls.OutputChannels(params)
      self.assertEqual(4, actual_output_channels)

  def testNormalizedDepthwiseConv2DLayerFPropMeta(self):
    params = (
        conv_layers_with_time_padding.NormalizedDepthwiseConv2DLayer.Params())
    params.name = 'conv'
    params.filter_shape = [3, 1, 2, 1]
    params.weight_tiling_factor = 2
    batch, time, frequency, in_channel = 2, 4, 1, 4
    output_channels = 4
    inputs_shape = tshape.Shape([batch, time, frequency, in_channel])
    paddings_shape = tshape.Shape([batch, time])
    with self.session():
      out = params.cls.FPropMeta(params, inputs_shape, paddings_shape)
      expected_flops = batch * time * frequency * params.filter_shape[
          0] * output_channels * 5
      self.assertEqual(expected_flops, out.flops)
      out_shapes = out.out_shapes
      self.assertEqual(out_shapes[0].ToTensorShape().as_list(),
                       [batch, time, frequency, output_channels])
      self.assertEqual(out_shapes[1].ToTensorShape().as_list(), [batch, time])

  def testNormalizedDepthwiseConv2DLayerFProp(self):
    expected_output = [[0.91136134, 1.25781929, 1.76708317, 0.9021343],
                       [0.52296412, 0.7703352, 0.65711987, 0.23177178]]
    with self.session(use_gpu=True) as sess:
      output = self._testNormalizedDepthwiseConv2DHelper()
      output_sum = tf.squeeze(tf.reduce_sum(output, -1))
      tf.global_variables_initializer().run()
      output_sum_val = sess.run(output_sum)
    self.assertAllClose(expected_output, output_sum_val)

  def testCausalNormalizedDepthwiseConv2DLayerFProp(self):
    expected_output = [[0.00819603, 0.91136134, 1.25781929, 1.76708317],
                       [-0.07673456, 0.52296412, 0.7703352, 0.65711987]]
    with self.session(use_gpu=True) as sess:
      output = self._testNormalizedDepthwiseConv2DHelper(is_causal=True)
      output_sum = tf.squeeze(tf.reduce_sum(output, -1))
      tf.global_variables_initializer().run()
      output_sum_val = sess.run(output_sum)
    self.assertAllClose(expected_output, output_sum_val)

  def testNormalizedDepthwiseConv2DLayerBackProp(self):
    with self.session(use_gpu=True) as sess:
      output = self._testNormalizedDepthwiseConv2DHelper(dropconnect_prob=0.1)
      loss = tf.reduce_sum(output)
      all_vars = tf.trainable_variables()
      grads = tf.gradients(loss, all_vars)
      tf.global_variables_initializer().run()
      sym_grads = [sg.eval() for sg in grads]
      num_grads = [
          test_utils.ComputeNumericGradient(sess, loss, v) for v in all_vars
      ]
      for sg, ng in zip(sym_grads, num_grads):
        self.assertAllClose(sg, ng, rtol=1e-02, atol=1e-02)

  def testCausualNormalizedDepthwiseConv2DLayerBackProp(self):
    with self.session(use_gpu=True) as sess:
      output = self._testNormalizedDepthwiseConv2DHelper(
          is_causal=True, dropconnect_prob=0.1)
      loss = tf.reduce_sum(output)
      all_vars = tf.trainable_variables()
      grads = tf.gradients(loss, all_vars)
      tf.global_variables_initializer().run()
      sym_grads = [sg.eval() for sg in grads]
      num_grads = [
          test_utils.ComputeNumericGradient(sess, loss, v) for v in all_vars
      ]
      for sg, ng in zip(sym_grads, num_grads):
        self.assertAllClose(sg, ng, rtol=1e-02, atol=1e-02)


class GlobalPoolingLayerTest(test_utils.TestCase):
  """Tests for GlobalPoolingLayer."""

  def _testHelper(self, pooling_type, inputs, input_paddings, expected_output,
                  expected_output_padding):
    param = conv_layers_with_time_padding.GlobalPoolingLayer.Params().Set(
        name='test_layer', pooling_type=pooling_type)
    pooling_layer = param.Instantiate()
    with self.session(use_gpu=True) as sess:
      inputs = tf.convert_to_tensor(inputs, dtype=tf.float32)
      input_paddings = None if input_paddings is None else tf.convert_to_tensor(
          input_paddings, dtype=tf.float32)
      output, output_paddings = pooling_layer.FPropDefaultTheta(
          inputs, input_paddings)
      tf.global_variables_initializer().run()
      if input_paddings is None:
        self.assertIsNone(output_paddings)
        output_val = sess.run(output)
      else:
        output_val, output_paddings_val = sess.run([output, output_paddings])

    self.assertAllClose(expected_output, output_val)
    if input_paddings is not None:
      self.assertAllEqual(expected_output_padding, output_paddings_val)

  def testPooling(self):
    inputs = np.random.random([3, 5, 2, 4]) - 0.5
    expected_avg_output = np.mean(inputs, axis=(1, 2), keepdims=True)
    expected_max_output = np.amax(inputs, axis=(1, 2), keepdims=True)
    self._testHelper('AVG', inputs, None, expected_avg_output, None)
    self._testHelper('MAX', inputs, None, expected_max_output, None)

  def testPoolingWithPadding(self):
    inputs = np.random.random([4, 3, 2, 4]) - 0.5
    paddings = np.array([[0, 0, 0], [0, 0, 1], [0, 1, 1], [1, 1, 1]])
    expected_paddings = np.array([[0], [0], [0], [1]])
    expected_avg_output = np.array([
        np.mean(inputs[0][:3], axis=(0, 1), keepdims=True),
        np.mean(inputs[1][:2], axis=(0, 1), keepdims=True),
        np.mean(inputs[2][:1], axis=(0, 1), keepdims=True),
        np.zeros((1, 1, 4))
    ])
    expected_max_output = np.array([
        np.amax(inputs[0][:3], axis=(0, 1), keepdims=True),
        np.amax(inputs[1][:2], axis=(0, 1), keepdims=True),
        np.amax(inputs[2][:1], axis=(0, 1), keepdims=True),
        np.zeros((1, 1, 4))
    ])

    self._testHelper('AVG', inputs, paddings, expected_avg_output,
                     expected_paddings)
    self._testHelper('MAX', inputs, paddings, expected_max_output,
                     expected_paddings)

  def testPoolingWithUnknowShapeInput(self):
    """Tests GlobalPooling layer with unknown shape tensor."""

    @tf.Defun(tf.float32)
    def remove_shape(tensor):
      return tensor

    g = tf.Graph()
    with g.as_default(), tf.Session(graph=g) as _:
      tf.set_random_seed(24332)
      input_shape = [3, 5, 2, 4]
      inputs = np.random.random(input_shape) - 0.5
      expected_avg_output = np.mean(inputs, axis=(1, 2), keepdims=True)
      input_tensor = tf.convert_to_tensor(inputs, dtype=tf.float32)
      # initial shape is [3, 5, 2, 4]
      self.assertEqual(py_utils.GetShape(input_tensor), input_shape)
      # remove shape using a tf Defun and verify dynamic tensor shape.
      input_tensor = remove_shape(input_tensor)
      self.assertIsInstance(py_utils.GetShape(input_tensor), tf.Tensor)
      self.assertIsNone(input_tensor.shape.rank)
      self._testHelper('AVG', input_tensor, None, expected_avg_output, None)


if __name__ == '__main__':
  tf.test.main()
