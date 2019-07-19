# Lint as: python2, python3
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
"""Tests for static_map_op."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from lingvo import compat as tf
from lingvo.core import test_utils
from lingvo.core.ops import py_x_ops

FLAGS = tf.flags.FLAGS


class StaticMapOpsTest(test_utils.TestCase):

  def testStaticMap(self):
    with self.session():
      self.assertAllEqual([[1, 3, 5], [7, 9, 11]],
                          py_x_ops.static_map_string_int(
                              x=[['a', 'b', 'c'], ['d', 'e', 'f']],
                              keys=['d', 'e', 'f', 'a', 'b', 'c'],
                              vals=[7, 9, 11, 1, 3, 5]).eval())
      self.assertAllEqual([[3, 4, 5], [0, 1, 2]],
                          py_x_ops.static_map_string_int(
                              x=[['a', 'b', 'c'], ['d', 'e', 'f']],
                              keys=['d', 'e', 'f', 'a', 'b', 'c']).eval())
      self.assertAllEqual([[2, -1, -1], [0, -1, 1]],
                          py_x_ops.static_map_string_int(
                              x=[['a', 'b', 'c'], ['d', 'e', 'f']],
                              keys=['d', 'f', 'a']).eval())

      # Error cases.
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError, 'sizes'):
        py_x_ops.static_map_string_int(
            x=[['a', 'b', 'c'], ['d', 'e', 'f']],
            keys=['d', 'f', 'a'],
            vals=[1, 2]).eval()
      with self.assertRaisesRegexp(tf.errors.InvalidArgumentError,
                                   'duplicates'):
        py_x_ops.static_map_string_int(
            x=[['a', 'b', 'c'], ['d', 'e', 'f']], keys=['d', 'f', 'd']).eval()


if __name__ == '__main__':
  tf.test.main()