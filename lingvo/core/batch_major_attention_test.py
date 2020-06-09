# Lint as: python3
# Copyright 2020 The TensorFlow Authors. All Rights Reserved.
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
"""Tests for batch_major_attention."""

from absl.testing import parameterized
from lingvo import compat as tf
from lingvo.core import base_layer
from lingvo.core import batch_major_attention as attention
from lingvo.core import hyperparams
from lingvo.core import py_utils
from lingvo.core import test_utils
import numpy as np
from six.moves import range
from six.moves import zip


class MultiHeadSelfAttentionTest(test_utils.TestCase, parameterized.TestCase):
  """Test attention models."""

  def _AttentionInputs(self, input_dim=4, dtype=tf.float32):
    np.random.seed(6348575)
    batch_size = 6
    seq_len = 6
    input_vecs_p = [
        np.random.rand(seq_len, input_dim) for _ in range(batch_size)
    ]
    input_vecs = tf.stack([tf.constant(x, dtype=dtype) for x in input_vecs_p])
    # pyformat: disable
    input_padding_p = [[0, 0, 1, 1, 0, 0], [1, 0, 0, 0, 1, 0],
                       [0, 0, 1, 0, 1, 0], [0, 0, 1, 1, 0, 0],
                       [1, 0, 0, 0, 1, 0], [0, 0, 1, 0, 1, 0]]
    # pyformat: enable
    input_padding = tf.constant(input_padding_p, dtype=dtype)

    return input_vecs, input_padding, input_vecs_p, input_padding_p

  def testDotProductAttention(self):
    (input_vecs, input_padding, input_vecs_p,
     input_padding_p) = self._AttentionInputs()
    p = attention.MultiHeadedAttention.Params().Set(
        name='self_atten', input_dim=4, hidden_dim=4)
    l = p.Instantiate()

    probs = l.AttenProbs(
        l.theta,
        tf.expand_dims(input_vecs, 2),
        tf.expand_dims(input_vecs, 2),
        input_padding,
        segment_mask=None)

    with self.session(use_gpu=False) as sess:
      tf.global_variables_initializer().run()
      prob_out = sess.run(tf.squeeze(probs))

    # Use numpy to perform the same computation to generate expected results.
    input_vecs_p = np.array(input_vecs_p)
    target_vecs_p = np.transpose(input_vecs_p, (0, 2, 1))
    expected_logit = np.matmul(input_vecs_p, target_vecs_p)
    expected_logit = np.transpose(expected_logit, (0, 2, 1))
    elexp = np.exp(expected_logit)
    input_padding_p = np.array(input_padding_p)
    input_padding_p = np.expand_dims(input_padding_p, axis=1)
    input_padding_p = np.tile(input_padding_p, (1, 6, 1))
    elexp *= (1 - input_padding_p)
    expected_prob_out = elexp / np.expand_dims(np.sum(elexp, axis=-1), axis=-1)
    expected_prob_out = np.reshape(expected_prob_out, (6, 6, 6))
    self.assertAllClose(expected_prob_out, prob_out)

  def testMultiHeadedAttentionDotProduct(self):
    # input_batch:6, seq_len:6. Test n = 2 case.
    with self.session(use_gpu=True) as sess:
      input_vecs, input_padding, _, _ = self._AttentionInputs()
      p = attention.MultiHeadedAttention.Params().Set(
          name='self_atten', num_heads=2, input_dim=4, hidden_dim=4)

      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)

      l = p.Instantiate()
      tf.global_variables_initializer().run()
      ctx_vec, _ = l.FProp(
          l.theta,
          input_vecs,
          input_vecs,
          input_vecs,
          input_padding,
          segment_mask=None)
      context_vec_out = sess.run(ctx_vec)
      context_vec_out = np.reshape(context_vec_out, (6, 24))
      self.assertAllClose(
          [27.417763, 31.783672, 19.99568, 23.907103, 21.078259, 28.429199],
          np.sum(context_vec_out, axis=1))

  def testMultiHeadedAttentionDotProductSegmentMask(self):
    # input_batch:6, seq_len:6. Test n = 2 case.
    with self.session(use_gpu=True) as sess:
      input_vecs, input_padding, _, _ = self._AttentionInputs()
      p = attention.MultiHeadedAttention.Params().Set(
          name='self_atten',
          num_heads=2,
          input_dim=4,
          hidden_dim=4,
          packed_input=True)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)

      segment_id = tf.zeros([6, 6])
      segment_mask = attention.SegmentMask(segment_id, segment_id)
      padding = tf.tile(tf.reshape(input_padding, [6, 1, 1, 6]), [1, 1, 6, 1])
      padding_mask = padding * segment_mask.dtype.max * tf.constant(
          -0.7, dtype=segment_mask.dtype)
      segment_mask += padding_mask

      l = p.Instantiate()
      tf.global_variables_initializer().run()
      ctx_vec, _ = l.FProp(
          l.theta,
          input_vecs,
          input_vecs,
          input_vecs,
          input_padding,
          segment_mask=segment_mask)
      context_vec_out = sess.run(ctx_vec)
      context_vec_out = np.reshape(context_vec_out, (6, 24))
      self.assertAllClose(
          [27.417763, 31.783672, 19.99568, 23.907103, 21.078259, 28.429199],
          np.sum(context_vec_out, axis=1))


class MultiHeadedAttentionXLOracle(object):
  """Oracle layer used for computing ground truths for MultiHeadedAttention.

  Written in a non-vectorized way.
  """

  def __init__(self, u, v, pos_proj, sinusoid_emb):
    """Constructor.

    Args:
      u: A numpy ndarray of shape [N, H]
      v: A numpy ndarray of shape [N, H]
      pos_proj: A numpy ndarray of shape [embed_dim, N, H]
      sinusoid_emb: A numpy ndarray of shape [seqlen, emb_dim].
    """
    assert u.shape == v.shape
    assert u.shape == pos_proj.shape[1:]
    assert sinusoid_emb.shape[-1] == pos_proj.shape[0]
    # [N, H]
    self._u = u
    # [N, H]
    self._v = v
    # [?, N, H]
    self._pos_proj = pos_proj

    self._num_heads = u.shape[0]
    self._atten_dim = u.shape[-1]
    self._hidden_dim = u.shape[0] * u.shape[-1]
    self._sinusoid_emb = sinusoid_emb

  def _GetPositionEnc(self, tgt_t, src_t, head, seqlen):
    """Gets positional encoding.

    Args:
      tgt_t: A Python int, time step of target seq.
      src_t: A Python int, time step of source seq.
      head: A Python int, num of heads of the attention.
      seqlen: A Python int, sequence length of target/source seq.

    Returns:
      A numpy array of shape [head, emb_dim // head].
    """
    # [emb_dim]
    sinusoid_enc = self._sinusoid_emb[tgt_t - src_t + seqlen - 1]
    return np.einsum('DNH,D->NH', self._pos_proj, sinusoid_enc)[head]

  def AttenProbs(self, key, query, paddings, per_step_padding):
    """Computes attention probs in a non vectorized way.

    Args:
      key: A numpy ndarray of shape [batch, seqlen, heads, dim].
      query: A numpy ndarray of the same shape as `key`.
      paddings: A numpy ndarray of shape [batch, seqlen].
      per_step_padding: A numpy ndarray of shape [batch, seqlen, seqlen].

    Returns:
      A numpy ndarray of shape [batch, query_seqlen, key_seqlen]
    """

    assert query.ndim == 4
    assert paddings.ndim == 2
    assert key.shape == query.shape

    batch, seqlen = query.shape[:2]
    tgtlen, srclen = seqlen, seqlen
    assert query.shape[2] == self._num_heads
    assert query.shape[3] == self._atten_dim
    assert paddings.shape == query.shape[:2]

    logits = np.zeros((batch, self._num_heads, tgtlen, srclen))
    probs = np.zeros((batch, self._num_heads, tgtlen, srclen))

    def Normalize(vec):
      expx = np.exp(vec)
      expxsum = np.sum(expx, axis=-1)
      return expx / expxsum

    # [b, tgtlen, srclen]
    paddings = np.broadcast_to(
        np.reshape(paddings, (batch, 1, seqlen)), (batch, seqlen, seqlen))
    for b in range(batch):
      for h in range(self._num_heads):
        for i in range(tgtlen):
          for j in range(srclen):
            pos_enc = self._GetPositionEnc(i, j, h, seqlen)
            logits[b][h][i][j] = (
                np.dot(query[b][i][h], key[b][j][h]) +
                np.dot(query[b][i][h], pos_enc) +
                np.dot(self._u[h], key[b][j][h]) + np.dot(self._v[h], pos_enc))

          total_padding = paddings[b][i] + per_step_padding[b][i]
          logits[b][h][i] = np.where(total_padding > 0,
                                     np.finfo(np.float32).max * (-0.7),
                                     logits[b][h][i])
          probs[b][h][i] = Normalize(logits[b][h][i])
    return probs


def _AttentionInputs(input_dim=4, dtype=tf.float32, is_causal=True):
  np.random.seed(6348575)
  batch_size = 6
  seq_len = 6
  query_vec_p = [np.random.rand(seq_len, input_dim) for _ in range(batch_size)]
  query_vec_p = np.array(query_vec_p).astype(dtype.as_numpy_dtype)
  query_vec = tf.convert_to_tensor(query_vec_p)

  memory_vec_p = [np.random.rand(seq_len, input_dim) for _ in range(batch_size)]
  memory_vec_p = np.array(memory_vec_p).astype(dtype.as_numpy_dtype)
  memory_vec = tf.convert_to_tensor(memory_vec_p)
  # pyformat: disable
  paddings_p = np.array(
      [[0, 0, 1, 1, 1, 1], [0, 1, 1, 1, 1, 1],
       [0, 0, 0, 0, 1, 1], [0, 0, 1, 1, 1, 1],
       [0, 0, 0, 1, 1, 1], [0, 0, 0, 0, 0, 1]]).astype(dtype.as_numpy_dtype)
  paddings = tf.convert_to_tensor(paddings_p)
  # causal padding.
  if is_causal:
    per_step_padding_p = [
        [0, 1, 1, 1, 1, 1], [0, 0, 1, 1, 1, 1],
        [0, 0, 0, 1, 1, 1], [0, 0, 0, 0, 1, 1],
        [0, 0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0]]
  else:
    per_step_padding_p = np.zeros((seq_len, seq_len))
  per_step_padding_p = [per_step_padding_p for _ in range(batch_size)]
  per_step_padding_p = np.array(per_step_padding_p).astype(dtype.as_numpy_dtype)
  per_step_padding = tf.convert_to_tensor(per_step_padding_p)

  # pyformat: enable
  return (query_vec, memory_vec, paddings, per_step_padding, query_vec_p,
          memory_vec_p, paddings_p, per_step_padding_p)


class MultiHeadedAttentionTest(test_utils.TestCase, parameterized.TestCase):
  """Test dot-product multiheaded attention."""

  def _AttentionExtendStepInputs(self,
                                 input_dim=4,
                                 num_heads=2,
                                 dtype=tf.float32):
    np.random.seed(6348575)
    batch_size = 6
    seq_len = 6
    query_vec_p = [np.random.rand(1, input_dim) for _ in range(batch_size)]
    query_vec = tf.stack([tf.constant(x, dtype=dtype) for x in query_vec_p])
    # pyformat: disable
    per_step_padding_p = [[0, 1, 1, 1, 1, 1]]
    per_step_padding_p = [per_step_padding_p for _ in range(batch_size)]
    # pyformat: enable
    per_step_padding = tf.stack(
        [tf.constant(x, dtype=dtype) for x in per_step_padding_p])
    source_vecs = tf.zeros(
        [seq_len, batch_size, num_heads, input_dim // num_heads], dtype=dtype)
    source_ctxs = tf.zeros(
        [seq_len, batch_size, num_heads, input_dim // num_heads], dtype=dtype)
    return source_vecs, source_ctxs, query_vec, per_step_padding

  def testAttenProbs(self):
    (query_vec, key_vec, paddings, per_step_padding, query_vec_p, key_vec_p,
     paddings_p, per_step_padding_p) = _AttentionInputs()
    p = attention.MultiHeadedAttention.Params().Set(
        name='atten', input_dim=4, hidden_dim=4)
    l = p.Instantiate()
    probs = l.AttenProbs(
        l.theta,
        tf.expand_dims(query_vec, 2),
        tf.expand_dims(key_vec, 2),
        paddings,
        segment_mask=None,
        per_step_padding=per_step_padding)

    with self.session(use_gpu=False) as sess:
      tf.global_variables_initializer().run()
      prob_out = sess.run(tf.squeeze(probs))

    # Use numpy to perform the same computation to generate expected results.
    query_vec_p = np.array(query_vec_p)
    key_vec_p = np.array(key_vec_p)
    key_vec_p = np.transpose(key_vec_p, (0, 2, 1))
    expected_logit = np.matmul(query_vec_p, key_vec_p)
    paddings_p = np.array(paddings_p)
    paddings_p = np.expand_dims(paddings_p, axis=1)
    paddings_p = np.tile(paddings_p, (1, 6, 1))
    per_step_padding_p = np.array(per_step_padding_p)
    paddings_p = 1.0 * np.logical_or(paddings_p, per_step_padding_p)
    elexp = np.exp(expected_logit)
    elexp *= (1.0 - paddings_p)
    elexp += 1e-9
    expected_prob_out = elexp / np.expand_dims(np.sum(elexp, axis=-1), axis=-1)
    expected_prob_out = np.reshape(expected_prob_out, (6, 6, 6))
    self.assertAllClose(expected_prob_out, prob_out)

  def testFPropCrossAttention(self):
    # input_batch:6, seq_len:6. Test n = 2 case.
    with self.session(use_gpu=True) as sess:
      query_vec, memory_vec, paddings, per_step_padding, _, _, _, _ = (
          _AttentionInputs())
      p = attention.MultiHeadedAttention.Params().Set(
          name='cross_atten', num_heads=2, input_dim=4, hidden_dim=4)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      tf.global_variables_initializer().run()
      ctx_vec, _ = l.FProp(
          l.theta,
          query_vec,
          memory_vec,
          memory_vec,
          paddings,
          segment_mask=None,
          per_step_padding=per_step_padding)
      context_vec_out = sess.run(ctx_vec)
      context_vec_out = np.reshape(context_vec_out, (6, 24))
      self.assertAllClose(
          [24.624561, 27.805634, 23.358835, 11.085404, 27.165989, 23.750813],
          np.sum(context_vec_out, axis=1))

  @parameterized.named_parameters(
      {
          'testcase_name': '_short_seq',
          'use_short_seq_opt': True,
      }, {
          'testcase_name': '_long_seq',
          'use_short_seq_opt': False,
      })
  def testExtendStepSelfAttention(self, use_short_seq_opt):
    # input_batch:6, seq_len:6, query_len: 1. Test n = 2 case.
    with self.session(use_gpu=True) as sess:
      source_vecs, source_ctxs, query_vec, per_step_padding = (
          self._AttentionExtendStepInputs())
      p = attention.MultiHeadedAttention.Params().Set(
          name='atten', num_heads=2, input_dim=4, hidden_dim=4)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      tf.global_variables_initializer().run()
      ctx_vec, new_src_vecs, _ = l.ExtendStep(l.theta, query_vec, source_vecs,
                                              source_ctxs, None, None,
                                              per_step_padding, 0,
                                              use_short_seq_opt)
      context_vec_out = sess.run(ctx_vec)
      new_source_vecs = sess.run(new_src_vecs)
      context_vec_out = np.reshape(context_vec_out, (6, 4))
      self.assertAllClose(
          [5.381485, 5.384035, 4.493689, 3.544395, 3.424472, 3.311054],
          np.sum(context_vec_out, axis=1))
      new_source_vecs = np.reshape(new_source_vecs, (6, 24))
      self.assertAllClose([4.116683, 0.0, 0.0, 0.0, 0.0, 0.0],
                          np.sum(new_source_vecs, axis=1))


class MultiHeadedAttentionXLTest(test_utils.TestCase, parameterized.TestCase):
  """Test dot-product multiheaded attention."""

  def _AttentionExtendStepInputs(self,
                                 input_dim,
                                 batch_size,
                                 seq_len,
                                 dtype=tf.float32):
    np.random.seed(6348575)
    query_vec_p = [
        np.random.rand(seq_len, input_dim) for _ in range(batch_size)
    ]
    query_vec = tf.stack([tf.constant(x, dtype=dtype) for x in query_vec_p])
    paddings_p = [[0] * seq_len] * batch_size
    paddings = tf.constant(paddings_p, dtype=dtype)
    return query_vec, paddings

  @parameterized.named_parameters(('OneHead', 1), ('OneHeadCausal', 1, True),
                                  ('MultiHead', 2),
                                  ('MultiHeadCausal', 2, True))
  def testAttenProbs(self, num_heads, is_causal=False):
    batch, slen = 6, 6
    atten_dim = 4
    input_dim = num_heads * atten_dim
    (input_vecs, _, input_padding, per_step_padding, input_vecs_p, _,
     input_padding_p, per_step_padding_p) = _AttentionInputs(
         input_dim=input_dim, is_causal=is_causal)
    p = attention.MultiHeadedAttentionXL.Params().Set(
        name='self_atten',
        input_dim=input_dim,
        num_heads=num_heads,
        hidden_dim=input_dim,
        rel_pos_emb_dim=input_dim)

    l = p.Instantiate()
    query = tf.reshape(input_vecs, (batch, slen, num_heads, atten_dim))
    probs = l.AttenProbs(
        l.theta,
        query,
        query,
        input_padding,
        segment_mask=None,
        per_step_padding=per_step_padding)

    # [1, 2 * slen - 1]
    positions = np.expand_dims(np.arange(-(slen - 1), slen), 0)
    sinusoid_emb = l.pos_emb.FPropWithPosition(l.theta.pos_emb,
                                               tf.convert_to_tensor(positions))
    # [ 2 * slen - 1, emb_dim=input_dim]
    sinusoid_emb = tf.squeeze(sinusoid_emb, 0)

    with self.session(use_gpu=False) as sess:
      tf.global_variables_initializer().run()
      u, v, pos_proj = sess.run([l.vars.u, l.vars.v, l.pos_proj.vars.w])
      actual_probs = sess.run(probs)
      sinusoid_emb_p = sess.run(sinusoid_emb)

    # Compute ground truth with oracle class.

    # Use numpy to perform the same computation to generate expected results.
    # [B, tgt_t, H]
    input_vecs_p = np.array(input_vecs_p)
    # [B, tgt_t, N, H]
    input_vecs_p = np.reshape(input_vecs_p, (batch, slen, num_heads, atten_dim))
    input_padding_p = np.array(input_padding_p)
    oracle = MultiHeadedAttentionXLOracle(u, v, pos_proj, sinusoid_emb_p)
    expected_probs = oracle.AttenProbs(input_vecs_p, input_vecs_p,
                                       input_padding_p, per_step_padding_p)
    self.assertAllClose(expected_probs, actual_probs)

  def testFPropSelfAttention(self):
    # input_batch:6, seq_len:6. Test n = 2 case.
    with self.session(use_gpu=True) as sess:
      query_vec, _, paddings, _, _, _, _, _ = _AttentionInputs()
      num_heads, input_dim, hidden_dim = 2, 4, 4
      p = attention.MultiHeadedAttentionXL.Params().Set(
          name='self_atten',
          num_heads=num_heads,
          input_dim=input_dim,
          hidden_dim=hidden_dim,
          rel_pos_emb_dim=num_heads * hidden_dim)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)

      l = p.Instantiate()
      ctx_vec, _ = l.FPropDefaultTheta(
          query_vec, query_vec, query_vec, paddings, segment_mask=None)

      tf.global_variables_initializer().run()
      context_vec_out = sess.run(ctx_vec)
      context_vec_out = np.reshape(context_vec_out, (6, 24))
      self.assertAllClose(
          [32.33513, 28.584404, 20.54517, 23.407812, 18.616188, 24.212755],
          np.sum(context_vec_out, axis=1))

  def testExtendStepSelfAttention(self):
    num_heads, input_dim, hidden_dim, batch, seqlen = 2, 4, 4, 6, 6
    emb_dim = 4
    with self.session(use_gpu=True):
      tf.random.set_seed(12345)
      query_vec, paddings = self._AttentionExtendStepInputs(
          input_dim, batch, seqlen)
      p = attention.MultiHeadedAttentionXL.Params().Set(
          name='atten',
          num_heads=num_heads,
          input_dim=input_dim,
          hidden_dim=hidden_dim,
          rel_pos_emb_dim=emb_dim,
          random_seed=0)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      tf.global_variables_initializer().run()

      # Verify ExtendStep() via compare N ExtendStep() with one FProp() call on
      # a seq with length N.
      per_step_padding = 1 - tf.linalg.band_part(
          tf.ones((seqlen, seqlen)), -1, 0)
      per_step_padding = tf.stack([per_step_padding] * batch)
      expected_ctx_vec, _ = l.FPropDefaultTheta(
          query_vec,
          query_vec,
          query_vec,
          paddings,
          segment_mask=None,
          per_step_padding=per_step_padding)
      dims_per_head = hidden_dim // num_heads
      cached_source_vecs = tf.zeros([seqlen, batch, num_heads, dims_per_head])
      cached_source_ctxs = tf.zeros([seqlen, batch, num_heads, dims_per_head])

      encoded_all = []
      for i in range(seqlen):
        per_step_paddings = 1. - tf.cast(
            tf.sequence_mask([i + 1] * batch, seqlen), tf.float32)
        per_step_paddings = tf.expand_dims(per_step_paddings, 1)
        encoded, cached_source_vecs, cached_source_ctxs = l.ExtendStep(
            l.theta, query_vec[:, i:i + 1, :], cached_source_vecs,
            cached_source_ctxs, paddings, None, per_step_paddings, i)
        # [batch, 1, dims_per_head]
        encoded_all.append(encoded)
      # [batch, T, dims_per_head]
      actual_ctx_vec = tf.concat(encoded_all, axis=1)
      self.assertAllClose(expected_ctx_vec.eval(), actual_ctx_vec.eval())


class MultiHeadedAttentionRPEOracle(object):
  """Computes ground truths for MultiHeadedfAttentionRPE.

  Written in a non-vectorized way.
  """

  def __init__(self, num_heads, key_embs, value_embs):
    """Constructor.

    Args:
      num_heads: A Python int.
      key_embs: A numpy array of shape [2 * radius + 1, hidden_dim]
      value_embs: A numpy array of shape [2 * radius + 1, hidden_dim]
    """
    assert key_embs.shape == value_embs.shape
    self._num_heads = num_heads
    self._hidden_dim = key_embs.shape[-1]
    self._atten_dim = self._hidden_dim // self._num_heads
    assert self._atten_dim * self._num_heads == self._hidden_dim

    self._key_embs = np.reshape(
        key_embs, [key_embs.shape[0], self._num_heads, self._atten_dim])
    self._value_embs = np.reshape(
        value_embs, [value_embs.shape[0], self._num_heads, self._atten_dim])
    self._radius = key_embs.shape[0] // 2

  def _GetEmb(self, tgt_t, src_t, head, emb_wt):
    radius = self._radius
    distance = np.clip(src_t - tgt_t, -radius, radius)
    return emb_wt[distance][head]

  def GetKeyEmb(self, tgt_t, src_t, head):
    return self._GetEmb(tgt_t, src_t, head, self._key_embs)

  def GetValueEmb(self, tgt_t, src_t, head):
    return self._GetEmb(tgt_t, src_t, head, self._value_embs)

  def AttenProbs(self, key, query, paddings):
    assert query.ndim == 4
    assert paddings.ndim == 2
    assert key.shape == query.shape

    batch, seqlen = query.shape[:2]
    tgtlen, srclen = seqlen, seqlen
    assert query.shape[2] == self._num_heads
    assert query.shape[3] == self._atten_dim
    assert paddings.shape == query.shape[:2]

    # [B, N, T, T]
    logits = np.zeros((batch, self._num_heads, tgtlen, srclen))
    # [B, N, T, T]
    probs = np.zeros((batch, self._num_heads, tgtlen, srclen))

    paddings = np.broadcast_to(
        np.reshape(paddings, (batch, 1, 1, seqlen)),
        (batch, self._num_heads, seqlen, seqlen))

    def Normalize(vec):
      expx = np.exp(vec)
      expxsum = np.sum(expx, axis=-1)
      return expx / expxsum

    for b in range(batch):
      for h in range(self._num_heads):
        for i in range(tgtlen):
          for j in range(srclen):
            logits[b][h][i][j] = np.dot(query[b][i][h],
                                        key[b][j][h] + self.GetKeyEmb(i, j, h))
          logits[b][h][i] = np.where(paddings[b][h][i] > 0,
                                     np.finfo(np.float32).max * (-0.7),
                                     logits[b][h][i])
          probs[b][h][i] = Normalize(logits[b][h][i])
    return probs

  def AttenContext(self, probs, values):
    assert probs.ndim == 4
    assert values.ndim == 4

    assert probs.shape[0] == values.shape[0]  # batch
    assert probs.shape[1] == values.shape[2]  # head
    assert probs.shape[2] == values.shape[1]  # tgtlen
    assert probs.shape[3] == probs.shape[2]  # slen
    assert values.shape[-1] == self._atten_dim

    batch, _, tgtlen, srclen = probs.shape
    # [B, N, T, H]
    ctx = np.zeros((batch, self._num_heads, tgtlen, self._atten_dim))
    for b in range(batch):
      for h in range(self._num_heads):
        for i in range(tgtlen):
          for j in range(srclen):
            ctx[b][h][i] += probs[b][h][i][j] * (
                values[b][j][h] + self.GetValueEmb(i, j, h))
    # [B, T, N, H]
    return np.transpose(ctx, (0, 2, 1, 3))


class MultiHeadedAttentionRPETest(test_utils.TestCase, parameterized.TestCase):

  @parameterized.named_parameters(('OneHead', 1), ('MultiHead', 2))
  def testAttenProbs(self, num_heads):
    batch, slen = 6, 6
    atten_dim = 4
    radius = 3
    input_dim = num_heads * atten_dim
    (input_vecs, _, input_padding, _, input_vecs_p, _, input_padding_p,
     _) = _AttentionInputs(input_dim=input_dim)
    p = attention.MultiHeadedAttentionRPE.Params().Set(
        name='self_atten',
        input_dim=input_dim,
        num_heads=num_heads,
        hidden_dim=input_dim,
        rel_pos_radius=radius)

    l = p.Instantiate()
    query = tf.reshape(input_vecs, (batch, slen, num_heads, atten_dim))
    probs = l.AttenProbs(
        l.theta, query, query, input_padding, segment_mask=None)

    with self.session(use_gpu=False) as sess:
      tf.global_variables_initializer().run()
      # [radius * 2 + 1, hidden_dim], [B, tgt_t, src_t]
      key_emb, value_emb, actual_probs = sess.run(
          [l.key_emb.vars.w, l.value_emb.vars.w, probs])

    oracle = MultiHeadedAttentionRPEOracle(num_heads, key_emb, value_emb)

    # Use numpy to perform the same computation to generate expected results.
    # [B, tgt_t, N, H]
    input_vecs_p = np.reshape(input_vecs_p, (batch, slen, num_heads, atten_dim))
    expected_probs = oracle.AttenProbs(input_vecs_p, input_vecs_p,
                                       input_padding_p)
    self.assertAllClose(expected_probs, actual_probs)

  @parameterized.named_parameters(('OneHead', 1), ('MultiHead', 2))
  def testAttenContext(self, num_heads):
    batch, slen = 6, 6
    atten_dim = 4
    radius = 3
    input_dim = num_heads * atten_dim
    (input_vecs, _, _, _, input_vecs_p, _, _,
     _) = _AttentionInputs(input_dim=input_dim)
    p = attention.MultiHeadedAttentionRPE.Params().Set(
        name='self_atten',
        input_dim=input_dim,
        num_heads=num_heads,
        hidden_dim=input_dim,
        rel_pos_radius=radius)

    l = p.Instantiate()
    probs = np.random.rand(batch, num_heads, slen, slen).astype(np.float32)
    probs = np.exp(probs) / np.sum(np.exp(probs), axis=-1, keepdims=True)
    ctx = l._AttenContext(
        l.theta, tf.convert_to_tensor(probs),
        tf.reshape(input_vecs, (batch, slen, num_heads, atten_dim)))

    with self.session(use_gpu=False) as sess:
      tf.global_variables_initializer().run()
      key_emb, value_emb, actual_ctx = sess.run(
          [l.key_emb.vars.w, l.value_emb.vars.w, ctx])

    oracle = MultiHeadedAttentionRPEOracle(num_heads, key_emb, value_emb)

    # [B, tgt_t, N, H]
    input_vecs_p = np.reshape(input_vecs_p, (batch, slen, num_heads, atten_dim))
    expected_ctx = oracle.AttenContext(probs, input_vecs_p)
    self.assertAllClose(expected_ctx, actual_ctx)

  @parameterized.named_parameters(('OneHead', 1), ('MultiHead', 2))
  def testAttenLogitsOneStep(self, num_heads):
    batch, slen = 6, 6
    atten_dim = 4
    radius = 3
    input_dim = num_heads * atten_dim
    (input_vecs, _, _, per_step_padding, _, _, _, _) = _AttentionInputs(
        input_dim=input_dim, is_causal=True)
    p = attention.MultiHeadedAttentionRPE.Params().Set(
        name='self_atten',
        input_dim=input_dim,
        num_heads=num_heads,
        hidden_dim=input_dim,
        rel_pos_radius=radius)

    l = p.Instantiate()
    # [B, T, N, H]
    query = tf.reshape(input_vecs, (batch, slen, num_heads, atten_dim))

    # Causal self attention.
    # [B, N, T, S]
    logits = l._AttenLogits(l.theta, query, query, per_step_padding)

    one_step_logits = []
    # [S=T, B, N, H]
    key = tf.transpose(query, [1, 0, 2, 3])
    for i in range(slen):
      local_logits = l._AttenLogitsOneStep(l.theta, query[:, i, :, :], key, i)
      one_step_logits.append(local_logits)
    # [T, S, B, N]
    stacked_logits = tf.stack(one_step_logits)
    stacked_logits = tf.transpose(stacked_logits, [2, 3, 0, 1])

    with self.session(use_gpu=False) as sess:
      tf.global_variables_initializer().run()
      expected_logits, actual_logits = sess.run([logits, stacked_logits])
    self.assertAllClose(expected_logits, actual_logits)

  @parameterized.named_parameters(('OneHead', 1), ('MultiHead', 2))
  def testAttenContextsOneStep(self, num_heads):
    batch, slen = 6, 6
    atten_dim = 4
    radius = 3
    input_dim = num_heads * atten_dim
    (input_vecs, _, _, per_step_padding, _, _, _, _) = _AttentionInputs(
        input_dim=input_dim, is_causal=True)
    p = attention.MultiHeadedAttentionRPE.Params().Set(
        name='self_atten',
        input_dim=input_dim,
        num_heads=num_heads,
        hidden_dim=input_dim,
        rel_pos_radius=radius)

    l = p.Instantiate()
    # [B, N, T, S=T]
    # Make causal attention probs.
    probs = np.random.rand(batch, num_heads, slen, slen).astype(np.float32)
    per_step_padding = 1 - np.tril(np.ones((slen, slen))).astype(np.float32)
    probs *= per_step_padding
    # Normalize
    probs = np.exp(probs) / np.sum(np.exp(probs), axis=-1, keepdims=True)

    # Causal self attention.
    # [B, N, T, S]
    ctx = l._AttenContext(
        l.theta, tf.convert_to_tensor(probs),
        tf.reshape(input_vecs, (batch, slen, num_heads, atten_dim)))

    one_step_ctx = []
    # [B, T, N, H] -> [S=T, B, N, H]
    value = tf.reshape(input_vecs, (batch, slen, num_heads, atten_dim))
    value = tf.transpose(value, [1, 0, 2, 3])
    for i in range(slen):
      # [B, N, S]
      local_prob = probs[:, :, i, :]
      # [S, B, N]
      local_prob = tf.transpose(local_prob, [2, 0, 1])
      # [B, N, H]
      local_ctx = l._AttenContextOneStep(l.theta, local_prob, value, i)
      one_step_ctx.append(local_ctx)
    # [T, B, N, H]
    stacked_ctx = tf.stack(one_step_ctx)
    stacked_ctx = tf.transpose(stacked_ctx, [1, 0, 2, 3])

    with self.session(use_gpu=False) as sess:
      tf.global_variables_initializer().run()
      expected_ctx, actual_ctx = sess.run([ctx, stacked_ctx])
    self.assertAllClose(expected_ctx, actual_ctx)


class LocalCausalSelfAttentionTest(test_utils.TestCase, parameterized.TestCase):
  """Test local causual self attention."""

  def _LocalCasualPadding(self, b, t, l, r):
    padding = np.ones((b, t, t))
    for i in range(t):
      padding[:, i, max(0, i - l + 1):i + r + 1] = 0
    return tf.constant(padding, dtype=tf.float32)

  @parameterized.named_parameters(
      {
          'testcase_name': 'block_size_unspecified',
          'block_size': None,
          'left_context': 4,
          'right_context': 1
      }, {
          'testcase_name': 'left_context_only',
          'block_size': 3,
          'left_context': 4,
          'right_context': 0,
      }, {
          'testcase_name': 'block_longer_than_sequence',
          'block_size': 10,
          'left_context': 7,
          'right_context': 0,
      }, {
          'testcase_name': 'pos_emb_left_context_only',
          'block_size': 3,
          'left_context': 4,
          'right_context': 0,
          'pos_emb_dim': 8,
      }, {
          'testcase_name': 'pos_emb_left_and_right_context',
          'block_size': 3,
          'left_context': 4,
          'right_context': 2,
          'pos_emb_dim': 8,
      }, {
          'testcase_name': 'lite_pos_emb_left_and_right_context',
          'block_size': 3,
          'left_context': 4,
          'right_context': 2,
          'pos_emb_dim': 8,
          'skip_term_b': True,
      })
  def testFPropAgainstReference(self,
                                block_size,
                                left_context,
                                right_context,
                                pos_emb_dim=0,
                                num_heads=2,
                                input_dim=4,
                                hidden_dim=4,
                                skip_term_b=False,
                                use_additional_per_step_padding=False):
    tf.reset_default_graph()
    with self.session(use_gpu=True) as sess:
      query_vec, _, paddings, _, _, _, _, _ = _AttentionInputs(input_dim)
      if use_additional_per_step_padding:
        # Generate a random binary mask of shape [N, T, S].
        additional_per_step_padding_val = np.random.random_integers(
            low=0, high=1, size=(6, 6, 6))
        additional_per_step_padding = tf.constant(
            additional_per_step_padding_val, tf.float32)
      else:
        additional_per_step_padding = None

      # Use the reference implementation + local casual padding to verify
      # correctness.
      if pos_emb_dim == 0:
        p_cls = attention.LocalCausalSelfAttention
        expected_p_cls = attention.MultiHeadedAttention
      else:
        p_cls = attention.LocalCausalSelfAttentionXL
        expected_p_cls = attention.MultiHeadedAttentionXL
      p = p_cls.Params().Set(
          name='self_atten',
          num_heads=num_heads,
          input_dim=input_dim,
          hidden_dim=hidden_dim,
          block_size=block_size,
          left_context=left_context,
          right_context=right_context)
      expected_p = expected_p_cls.Params().Set(
          name='expected_self_atten',
          num_heads=num_heads,
          input_dim=input_dim,
          hidden_dim=hidden_dim)
      if pos_emb_dim != 0:
        p.rel_pos_emb_dim = pos_emb_dim
        expected_p.rel_pos_emb_dim = pos_emb_dim
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      expected_p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)

      l = p.Instantiate()
      expected_l = expected_p.Instantiate()

      tf.global_variables_initializer().run()
      ctx_vec, _ = l.FProp(
          l.theta,
          query_vec,
          query_vec,
          query_vec,
          paddings,
          segment_mask=None,
          per_step_padding=additional_per_step_padding)
      context_vec_out = sess.run(ctx_vec)
      per_step_padding = self._LocalCasualPadding(6, 6, left_context,
                                                  right_context)
      if additional_per_step_padding is not None:
        per_step_padding += additional_per_step_padding
      expected_ctx_vec, _ = expected_l.FProp(expected_l.theta, query_vec,
                                             query_vec, query_vec, paddings,
                                             None, per_step_padding)
      expected_context_vec_out = sess.run(expected_ctx_vec)

      # Don't compare if the query position is padded, or if all key positions
      # are padded.
      paddings_val = sess.run(paddings)
      per_step_padding_val = sess.run(per_step_padding)
      per_step_padding_val += paddings_val[:, :, np.newaxis]
      per_step_padding_val += paddings_val[:, np.newaxis, :]

      dont_compare = np.sum(
          per_step_padding_val > 0, axis=-1) == per_step_padding_val.shape[-1]
      expected_context_vec_out *= (1 - dont_compare)[..., np.newaxis]
      context_vec_out *= (1 - dont_compare)[..., np.newaxis]
      self.assertAllClose(context_vec_out, expected_context_vec_out)

  def testFPropWithDropout(self):
    with self.session(use_gpu=True) as sess:
      query_vec, _, paddings, _, _, _, _, _ = _AttentionInputs(input_dim=4)
      p = attention.LocalCausalSelfAttention.Params().Set(
          name='self_atten',
          num_heads=2,
          input_dim=4,
          hidden_dim=4,
          block_size=2,
          left_context=2,
          right_context=0,
          atten_dropout_prob=0.3,
      )
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      tf.global_variables_initializer().run()
      ctx_vec, _ = l.FProp(
          l.theta, query_vec, query_vec, query_vec, paddings, segment_mask=None)
      ctx_vec_val = sess.run(ctx_vec)
      print(ctx_vec_val)


class TransformerLayerTest(test_utils.TestCase, parameterized.TestCase):
  """Test Transformer decoder layers."""

  def _TransformerAttentionLayerInputs(self, input_dim=4, dtype=tf.float32):
    np.random.seed(6348575)
    query_vec = tf.transpose(
        tf.stack([
            tf.constant(np.random.rand(2, input_dim), dtype=dtype)
            for _ in range(5)
        ]), [1, 0, 2])
    paddings = tf.constant([[0, 0, 1, 1, 0], [1, 0, 0, 0, 1]], dtype=dtype)
    aux_vec = tf.transpose(
        tf.stack([
            tf.constant(np.random.rand(2, input_dim), dtype=dtype)
            for _ in range(7)
        ]), [1, 0, 2])
    aux_paddings = tf.constant([[0, 1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1]],
                               dtype=dtype)
    return query_vec, paddings, aux_vec, aux_paddings

  def testTransformerAttentionLayerFPropMaskedSelfAttention(self):
    with self.session(use_gpu=True) as sess:
      query_vec, paddings, _, _ = self._TransformerAttentionLayerInputs()

      p = attention.TransformerAttentionLayer.Params().Set(
          name='transformer_masked_self_atten',
          input_dim=4,
          is_masked=True,
          num_heads=2)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      ctx_vec, _ = l.FProp(l.theta, query_vec, None, paddings)

      tf.global_variables_initializer().run()
      actual_ctx = sess.run(ctx_vec)
      actual_ctx = np.reshape(actual_ctx, (10, 4))
      tf.logging.info(np.array_repr(actual_ctx))
      expected_ctx = [7.777687, 5.219166, 6.305151, 4.817311]
      self.assertAllClose(expected_ctx, np.sum(actual_ctx, axis=0))

  def testAttentionLayerFPropMaskedSelfAttentionPaddingOverride(self):
    with self.session(use_gpu=True) as sess:
      query_vec, paddings, _, _ = self._TransformerAttentionLayerInputs()

      p = attention.TransformerAttentionLayer.Params().Set(
          name='transformer_masked_self_atten',
          input_dim=4,
          is_masked=True,
          num_heads=2)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      triangle_padding = 1.0 - tf.linalg.band_part(
          tf.ones([5, 5], dtype=query_vec.dtype), -1, 0)
      per_step_padding_override = tf.tile(
          tf.expand_dims(triangle_padding, 0), [2, 1, 1])

      ctx_vec1, _ = l.FProp(l.theta, query_vec, None, paddings,
                            per_step_padding_override)
      expected_ctx1, _ = l.FProp(l.theta, query_vec, None, paddings)
      per_step_padding_override = tf.zeros([2, 5, 5])
      ctx_vec2, _ = l.FProp(l.theta, query_vec, None, paddings,
                            per_step_padding_override)

      tf.global_variables_initializer().run()
      actual_ctx1, actual_ctx2, actual_expected_ctx1 = sess.run(
          [ctx_vec1, ctx_vec2, expected_ctx1])
      tf.logging.info(np.array_repr(actual_ctx1))
      tf.logging.info(np.array_repr(actual_ctx2))
      expected_ctx2 = [7.9491496, 5.2976646, 6.5383415, 5.0169916]
      self.assertAllClose(actual_expected_ctx1, ctx_vec1)
      self.assertAllClose(expected_ctx2,
                          np.sum(np.reshape(actual_ctx2, (10, 4)), axis=0))

  def testTransformerAttentionLayerFPropCrossAttention(self):
    with self.session(use_gpu=True) as sess:
      (query_vec, _, aux_vec,
       aux_paddings) = self._TransformerAttentionLayerInputs()
      p = attention.TransformerAttentionLayer.Params().Set(
          name='transformer_cross_atten',
          input_dim=4,
          is_masked=False,
          num_heads=2)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      ctx_vec, _ = l.FProp(l.theta, query_vec, aux_vec, aux_paddings)

      tf.global_variables_initializer().run()
      actual_ctx = sess.run(ctx_vec)
      actual_ctx = np.reshape(actual_ctx, (10, 4))
      tf.logging.info(np.array_repr(actual_ctx))
      expected_ctx = [19.345360, 15.057412, 13.744134, 13.387347]
      self.assertAllClose(expected_ctx, np.sum(actual_ctx, axis=0))

  @parameterized.named_parameters(
      {
          'testcase_name': '_short_seq',
          'use_short_seq_opt': True,
      }, {
          'testcase_name': '_long_seq',
          'use_short_seq_opt': False,
      })
  def testTransformerAttentionLayerExtendStep(self, use_short_seq_opt):
    with self.session(use_gpu=True) as sess:
      query_vec, _, _, _ = self._TransformerAttentionLayerInputs()
      paddings = tf.zeros([2, 5])
      cached_key = tf.zeros([5, 2, 2, 2])
      cached_value = tf.zeros([5, 2, 2, 2])
      prefix_states = py_utils.NestedMap(key=cached_key, value=cached_value)

      p = attention.TransformerAttentionLayer.Params().Set(
          name='transformer_atten', input_dim=4, is_masked=True, num_heads=2)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()

      ctx_vec1, _ = l.FProp(l.theta, query_vec, None, paddings)

      ctx_vec2 = []
      for i in range(5):
        ctx_vec, prefix_states = l.ExtendStep(
            l.theta, tf.expand_dims(query_vec[:, i, :], 1), prefix_states, i,
            use_short_seq_opt)
        ctx_vec2.append(tf.squeeze(ctx_vec, 1))
      ctx_vec2 = tf.transpose(tf.stack(ctx_vec2), [1, 0, 2])

      tf.global_variables_initializer().run()
      ctx1, ctx2 = sess.run([ctx_vec1, ctx_vec2])
      self.assertAllClose(ctx1, ctx2)

  def _ConstructTransformerDecoderLayer(self, use_relative_atten=False):
    p = attention.TransformerDecoderLayer.Params()
    p.name = 'transformer_decoder_layer'
    p.input_dim = 4
    p.tr_fflayer_tpl.hidden_dim = 7
    p.tr_atten_tpl.num_heads = 2
    if use_relative_atten:
      p = attention.UseRelativeAttentionInTransformerLayer(p, 4)
    p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
    return attention.TransformerDecoderLayer(p)

  @parameterized.named_parameters(('SingleBatch', 1), ('DoubleBatch', 2))
  def testTransformerLayerFPropWithCrossAttention(self, multiplier):
    with self.session(use_gpu=True) as sess:
      (query_vec, _, aux_vec,
       aux_paddings) = self._TransformerAttentionLayerInputs()
      query_vec = tf.tile(query_vec, [multiplier, 1, 1])
      paddings = tf.zeros([2 * multiplier, 5])
      p = attention.TransformerLayer.Params()
      p.name = 'transformer_layer'
      p.input_dim = 4
      p.tr_fflayer_tpl.hidden_dim = 7
      p.tr_atten_tpl.num_heads = 2
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      ctx_vec, _ = l.FProp(l.theta, query_vec, paddings, aux_vec, aux_paddings)

      tf.global_variables_initializer().run()
      actual_ctx = sess.run(ctx_vec)
      actual_ctx = np.reshape(actual_ctx, (10 * multiplier, 4))
      tf.logging.info(np.array_repr(actual_ctx))
      expected_ctx = [
          4.7839108, 4.5303655, 5.5551023, 5.065767, 5.0493064, 3.2142467,
          2.8200178, 5.659971, 4.3814187, 2.60475
      ] * multiplier
      self.assertAllClose(expected_ctx, np.sum(actual_ctx, axis=1))

  @parameterized.named_parameters(('Base', False), ('RelativeAtten', True))
  def testTransformerDecoderLayerConstruction(self, use_relative_atten):
    _ = self._ConstructTransformerDecoderLayer(
        use_relative_atten=use_relative_atten)

  def testTransformerDecoderLayerFProp(self):
    with self.session(use_gpu=True) as sess:
      (query_vec, paddings, aux_vec,
       aux_paddings) = self._TransformerAttentionLayerInputs()
      l = self._ConstructTransformerDecoderLayer()

      layer_output, _ = l.FProp(l.theta, query_vec, paddings, aux_vec,
                                aux_paddings)

      tf.global_variables_initializer().run()
      actual_layer_output = sess.run(layer_output)
      actual_layer_output = np.reshape(actual_layer_output, (10, 4))
      tf.logging.info(np.array_repr(actual_layer_output))
      expected_layer_output = [16.939590, 24.121685, 19.975197, 15.924350]
      self.assertAllClose(expected_layer_output,
                          np.sum(actual_layer_output, axis=0))

  def _ConstructTransformerEncoderLayerStack(self):
    p = attention.StackedTransformerLayers.Params()
    p.name = 'encoder_layers'
    p.has_aux_atten = False
    p.mask_self_atten = False
    p.num_layers = 2
    p.mdl_dim = 4
    p.hidden_dim = 8
    p.num_atten_heads = 2
    p.dropout_prob = 0.2
    p.params_init = py_utils.WeightInit.Xavier()
    p.random_seed = 12345
    return p.Instantiate()

  def _ConstructTransformerDecoderLayerStack(self, dropout_prob=0.2):
    p = attention.StackedTransformerLayers.Params()
    p.name = 'decoder_layers'
    p.has_aux_atten = True
    p.mask_self_atten = True
    p.num_layers = 2
    p.mdl_dim = 4
    p.hidden_dim = 8
    p.num_atten_heads = 2
    p.dropout_prob = dropout_prob
    p.params_init = py_utils.WeightInit.Xavier()
    p.random_seed = 12345
    return p.Instantiate()

  def testTransformerEncoderLayerStackFProp(self):
    with self.session(use_gpu=True) as sess:
      (query_vec, paddings, _, _) = self._TransformerAttentionLayerInputs()
      l = self._ConstructTransformerEncoderLayerStack()
      layer_output, _ = l.FProp(l.theta, query_vec=query_vec, paddings=paddings)
      tf.global_variables_initializer().run()
      actual_layer_output = sess.run(layer_output)
      actual_layer_output = np.reshape(actual_layer_output, (10, 4))
      tf.logging.info(np.array_repr(actual_layer_output))
      expected_layer_output = [6.178955, -11.376661, 7.032681, -1.532627]
      self.assertAllClose(expected_layer_output,
                          np.sum(actual_layer_output, axis=0))

  def testTransformerDecoderLayerStackFProp(self):
    with self.session(use_gpu=True) as sess:
      (query_vec, paddings, aux_vec,
       aux_paddings) = self._TransformerAttentionLayerInputs()
      l = self._ConstructTransformerDecoderLayerStack()
      layer_output, _ = l.FProp(
          l.theta,
          query_vec=query_vec,
          paddings=paddings,
          aux_vec=aux_vec,
          aux_paddings=aux_paddings)
      tf.global_variables_initializer().run()
      actual_layer_output = sess.run(layer_output)
      actual_layer_output = np.reshape(actual_layer_output, (10, 4))
      tf.logging.info(np.array_repr(actual_layer_output))
      expected_layer_output = [9.926413, -4.491376, 27.051598, 2.112684]
      self.assertAllClose(expected_layer_output,
                          np.sum(actual_layer_output, axis=0))

  @parameterized.named_parameters(
      {
          'testcase_name': '_short_seq',
          'use_short_seq_opt': True,
      }, {
          'testcase_name': '_long_seq',
          'use_short_seq_opt': False,
      })
  def testTransformerDecoderLayerStackExtendStep(self, use_short_seq_opt):

    def _Rnd(seed):
      return tf.random.normal([5, 2, 2, 2], seed=seed)

    graph = tf.Graph()
    with graph.as_default():
      tf.random.set_seed(123456)
      query_vec, _, aux_vec, aux_paddings = (
          self._TransformerAttentionLayerInputs())
      paddings = tf.zeros([2, 5])
      layer_prefix_states_1 = py_utils.NestedMap(key=_Rnd(1), value=_Rnd(2))
      layer_prefix_states_2 = py_utils.NestedMap(key=_Rnd(3), value=_Rnd(4))
      prefix_states = py_utils.NestedMap(
          x_layers=[layer_prefix_states_1, layer_prefix_states_2])

      l = self._ConstructTransformerDecoderLayerStack(dropout_prob=0.)

      layer_output1, _ = l.FProp(l.theta, query_vec, paddings, aux_vec,
                                 aux_paddings)

      layer_output2 = []
      for i in range(5):
        layer_output, prefix_states = l.ExtendStep(
            l.theta, tf.expand_dims(query_vec[:, i, :], 1), aux_vec,
            aux_paddings, prefix_states, i, use_short_seq_opt)
        layer_output2.append(tf.squeeze(layer_output, 1))
      layer_output2 = tf.transpose(tf.stack(layer_output2), [1, 0, 2])

    with self.session(graph=graph, use_gpu=True) as sess:
      tf.global_variables_initializer().run()
      actual_layer_output1, actual_layer_output2 = sess.run(
          [layer_output1, layer_output2])

    self.assertAllClose(actual_layer_output1, actual_layer_output2)

  @parameterized.named_parameters(
      {
          'testcase_name': '_short_seq',
          'use_short_seq_opt': True,
      }, {
          'testcase_name': '_long_seq',
          'use_short_seq_opt': False,
      })
  def testTransformerDecoderLayerExtendStep(self, use_short_seq_opt):
    with self.session(use_gpu=True) as sess:
      (query_vec, _, aux_vec,
       aux_paddings) = self._TransformerAttentionLayerInputs()
      paddings = tf.zeros([2, 5])
      cached_key = tf.zeros([5, 2, 2, 2])
      cached_value = tf.zeros([5, 2, 2, 2])
      prefix_states = py_utils.NestedMap(key=cached_key, value=cached_value)

      l = self._ConstructTransformerDecoderLayer()

      layer_output1, _ = l.FProp(l.theta, query_vec, paddings, aux_vec,
                                 aux_paddings)

      layer_output2 = []
      for i in range(5):
        layer_output, prefix_states = l.ExtendStep(
            l.theta, tf.expand_dims(query_vec[:, i, :], 1), aux_vec,
            aux_paddings, prefix_states, i, use_short_seq_opt)
        layer_output2.append(tf.squeeze(layer_output, 1))
      layer_output2 = tf.transpose(tf.stack(layer_output2), [1, 0, 2])

      tf.global_variables_initializer().run()
      actual_layer_output1, actual_layer_output2 = sess.run(
          [layer_output1, layer_output2])
      self.assertAllClose(actual_layer_output1, actual_layer_output2)

  def testGPipeTransformerLayerConstruction(self):
    p = attention.GPipeTransformerLayer.Params()
    p.name = 'gpipe_transformer_layer'
    p.input_dim = 4
    p.tr_fflayer_tpl.hidden_dim = 7
    p.tr_atten_tpl.num_heads = 2
    p.tr_atten_tpl.residual_dropout_prob = 0.1
    p.cls.SetupDeterministicDropout(p)
    layer = p.Instantiate()
    self.assertEqual(0.1, layer.params.tr_atten_tpl.residual_dropout_prob)


class BuilderTest(test_utils.TestCase, parameterized.TestCase):

  def _testGraph(self, glu_with_tanh=False, dtype=tf.float32):
    tf.random.set_seed(398847392)
    np.random.seed(12345)
    atten_builder = attention.Builder.Params().Set(
        model_dim=4, num_heads=2, ff_hidden_dim=16, glu_with_tanh=glu_with_tanh)
    params = atten_builder.Instantiate().LConvStack(
        name='lightconv', kernel_sizes=[3, 3])
    params.dtype = dtype
    params.random_seed = 0
    params.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
    l = params.Instantiate()
    l_in = tf.constant(np.random.rand(2, 3, 4), dtype=dtype)
    l_padding = tf.zeros([2, 3], dtype=dtype)
    l_out = l.FPropDefaultTheta(
        py_utils.NestedMap(vec=l_in, paddings=l_padding))
    return l_out.vec

  @parameterized.parameters((False, 38.163662), (True, 35.88797))
  def testFprop(self, glu_with_tanh, expected_result):
    with self.session(use_gpu=False, graph=tf.Graph()) as sess:
      l_out = self._testGraph(glu_with_tanh)
      l_out = tf.reduce_sum(l_out)
      tf.global_variables_initializer().run()
      l_out_eval = sess.run(l_out)
      self.assertAllClose(expected_result, l_out_eval)

  def testBProp(self):
    with self.session(use_gpu=True) as sess:
      output = self._testGraph(dtype=tf.float64)
      loss = tf.reduce_sum(output)
      all_vars = tf.trainable_variables()
      grads = tf.gradients(loss, all_vars)
      tf.global_variables_initializer().run()
      sym_grads = [sg.eval() for sg in grads]
      num_grads = [
          test_utils.ComputeNumericGradient(sess, loss, v) for v in all_vars
      ]
      for ng, sg in zip(num_grads, sym_grads):
        self.assertAllClose(ng, sg, rtol=5e-02, atol=5e-02)

  @parameterized.named_parameters(
      {
          'testcase_name': '_baseline',
          'strides': [1, 1],
      }, {
          'testcase_name': '_stride_2',
          'strides': [2, 1],
      }, {
          'testcase_name': '_first_token',
          'strides': [2, 0],
      })
  def testTransformerStackWithStride(self, strides):
    with self.session(use_gpu=False) as sess:
      bs = 2
      sl = 10
      d = 16
      tf.random.set_seed(12345)
      atten_builder = attention.Builder.Params().Set(
          model_dim=d, num_heads=2, ff_hidden_dim=5).Instantiate()
      layers = []
      accumulate_stride = 1
      for layer_i, stride in enumerate(strides):
        accumulate_stride *= stride
        layers.append(
            atten_builder.TransformerEncoderLayer(
                name='atten_{}'.format(layer_i), stride=stride))
      p = atten_builder.Seq('model', *layers)
      p.params_init = py_utils.WeightInit.Xavier(scale=1.0, seed=0)
      l = p.Instantiate()
      input_embs = tf.constant(
          np.random.random(size=[bs, sl, d]), dtype=np.float)
      paddings = tf.zeros([bs, sl])
      l_out = l.FPropDefaultTheta(
          py_utils.NestedMap(vec=input_embs, paddings=paddings))
      enc_out = l_out.vec
      tf.global_variables_initializer().run()
      actual_enc_out = sess.run(enc_out)
      seq_len = sl // accumulate_stride if accumulate_stride != 0 else 1
      self.assertAllEqual([bs, seq_len, d], actual_enc_out.shape)


def _CreateDummyParams(field_names):
  p = hyperparams.Params()
  for name in field_names:
    p.Define(name, None, 'Dummy')
  return p


class DummyDecoderRNNT(base_layer.BaseLayer):

  @classmethod
  def Params(cls):
    p = super(DummyDecoderRNNT, cls).Params()
    p.name = 'dummy_decoder_rnnt'
    p.Define('emb', _CreateDummyParams(['vocab_size']), 'Dummy emb.')
    p.Define('target_seq_len', 20, 'Dummy target seq len.')
    p.Define('num_classes', None, 'Dummy num classes.')
    return p

  @classmethod
  def UpdateTargetVocabSize(cls, p, vocab_size, wpm_model=None):
    p.emb.vocab_size = vocab_size
    p.num_classes = vocab_size
    return p


class RelativeAttentionHelperTest(test_utils.TestCase, parameterized.TestCase):

  @parameterized.named_parameters(
      ('MultiHeadedAttentionXL', attention.MultiHeadedAttentionXL,
       attention.MultiHeadedAttention),
      ('LocalCausalSelfAttentionXL', attention.LocalCausalSelfAttentionXL,
       attention.LocalCausalSelfAttention))
  def testClearRelativeAttentionInTransformerLayer(self, atten_cls,
                                                   expected_atten_cls):
    """Tests scenarios in clear relative attention in transformer layer."""
    trans_p = attention.TransformerLayer.Params()
    # set attention params in transformer layer.
    input_dim = 4
    rel_pos_emb_dim = 4
    # Set rel_pos_emb_dim in attention params.
    trans_p.tr_atten_tpl.atten_tpl = (
        atten_cls.Params().Set(
            input_dim=input_dim, rel_pos_emb_dim=rel_pos_emb_dim))
    new_trans_p = attention.ClearRelativeAttentionInTransformerLayer(trans_p)
    tr_atten_tpl = new_trans_p.tr_self_atten_tpl.atten_tpl
    self.assertEqual(tr_atten_tpl.cls, expected_atten_cls)
    self.assertEqual(tr_atten_tpl.input_dim, input_dim)

  def testClearRelativeAttentionTransformerLayerNotSupportedError(self):
    transformer_params = DummyDecoderRNNT.Params()
    with self.assertRaises(ValueError):
      _ = attention.ClearRelativeAttentionInTransformerLayer(transformer_params)

  def testClearRelativeAttentionAttentionParamsNotSupportedError(self):
    trans_p = attention.TransformerLayer.Params()
    # MultiHeadedAttention is not supported in ClearRelativeAttention.
    attention_params = attention.MultiHeadedAttention.Params()
    trans_p.tr_atten_tpl.atten_tpl = attention_params
    with self.assertRaises(ValueError):
      _ = attention.ClearRelativeAttentionInTransformerLayer(trans_p)

  @parameterized.named_parameters(
      ('AttentionParamsNotSupported', _CreateDummyParams(
          ['name', 'cls']), attention.ATTEN_TRANSFORMER_XL),
      ('AttentionTypeNotSupported', attention.MultiHeadedAttention.Params(),
       'unsupported_atten_type'))
  def testUseRelativeAttentionInTransformerLayerValueError(
      self, attention_params, attention_type):
    """Tests unsupported Use Relative Attention cases."""
    transformer_param = attention.TransformerLayer.Params()
    transformer_param.tr_atten_tpl.atten_tpl = attention_params
    rel_pos_emb_dim = 4
    with self.assertRaises(ValueError):
      _ = attention.UseRelativeAttentionInTransformerLayer(
          transformer_param, rel_pos_emb_dim, atten_type=attention_type)

  def testUseRelativeAttentionInTransformerLayerNotSupportedError(self):
    """Tests unsupported input transformer params in Use Relative Attention."""
    transformer_params = DummyDecoderRNNT.Params()
    with self.assertRaises(ValueError):
      _ = attention.UseRelativeAttentionInTransformerLayer(
          transformer_params, 4, atten_type=attention.ATTEN_TRANSFORMER_XL)

  @parameterized.named_parameters(
      ('MultiHeadedAttention', attention.MultiHeadedAttention,
       attention.MultiHeadedAttentionXL, attention.ATTEN_TRANSFORMER_XL),
      ('LocalCausalSelfAttention', attention.LocalCausalSelfAttention,
       attention.LocalCausalSelfAttentionXL, attention.ATTEN_TRANSFORMER_XL),
      ('MultiHeadedAttentionRPE', attention.MultiHeadedAttention,
       attention.MultiHeadedAttentionRPE, attention.ATTEN_RPE))
  def testUseRelativeAttentionInTransformerLayer(self, atten_cls,
                                                 expected_atten_cls,
                                                 atten_type):
    """Tests different scenarios in Use Relative Attention."""
    trans_p = attention.TransformerLayer.Params()
    # set attenion params in transformer layer.
    input_dim = 4
    trans_p.tr_atten_tpl.atten_tpl = atten_cls.Params().Set(input_dim=input_dim)
    rel_pos_emb_dim = 4
    new_trans_p = attention.UseRelativeAttentionInTransformerLayer(
        trans_p, rel_pos_emb_dim, atten_type=atten_type)
    tr_atten_tpl = new_trans_p.tr_self_atten_tpl.atten_tpl
    self.assertEqual(tr_atten_tpl.cls, expected_atten_cls)
    self.assertEqual(tr_atten_tpl.rel_pos_emb_dim, rel_pos_emb_dim)
    self.assertEqual(tr_atten_tpl.input_dim, input_dim)


if __name__ == '__main__':
  tf.test.main()
