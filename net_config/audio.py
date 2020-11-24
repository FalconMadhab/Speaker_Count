import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Normal, Uniform, HalfNormal

from torchaudio_contrib import STFT, TimeStretch, MelFilterbank, ComplexNorm, ApplyFilterbank


def amplitude_to_db(spec, ref=1.0, amin=1e-10, top_db=80):
    """
    Amplitude spectrogram to the db scale
    """
    power = spec**2
    return power_to_db(power, ref, amin, top_db)


def power_to_db(spec, ref=1.0, amin=1e-10, top_db=80.0):
    """
    Power spectrogram to the db scale
    spec -> (*, freq, time)
    """
    if amin <= 0:
        raise ParameterError('amin must be strictly positive')

    if callable(ref):
        ref_value = ref(spec)
    else:
        ref_value = torch.tensor(ref)

    log_spec = 10*torch.log10( torch.clamp(spec, min=amin) )
    log_spec -= 10*torch.log10( torch.clamp(ref_value, min=amin) )
    
    if top_db is not None:
        if top_db < 0:
            raise ParameterError('top_db must be non-negative')
        
        log_spec = torch.clamp(log_spec, min=(log_spec.max() - top_db))

    #log_spec /= log_spec.max()
    return log_spec
    

def spec_whiten(spec, eps=1):    
    
    along_dim = lambda f, x: f(x, dim=-1).reshape(-1,1,1,1)
    
    lspec = torch.log10(spec + eps)

    batch = lspec.size(0)

    mean = along_dim(torch.mean, lspec.reshape(batch, -1))
    std = along_dim(torch.std, lspec.reshape(batch, -1))

    resu = (lspec - mean)/std

    return resu


def _num_stft_bins(lengths, fft_length, hop_length, pad):
    return (lengths + 2 * pad - fft_length + hop_length) // hop_length


class MelspectrogramStretch(nn.Module):

    def __init__(self, hop_length=None, num_mels=128, fft_length=2048, norm='whiten', stretch_param=[0.4, 0.4]):

        super(MelspectrogramStretch, self).__init__()
        
        self.prob = stretch_param[0]
        self.dist = Uniform(-stretch_param[1], stretch_param[1])
        self.norm = {
            'whiten':spec_whiten,
            'db' : amplitude_to_db
            }.get(norm, None)

        self.stft = STFT(fft_length=fft_length, hop_length=fft_length//4)
        self.pv = TimeStretch(hop_length=self.stft.hop_length, num_freqs=fft_length//2+1)
        self.cn = ComplexNorm(power=2.)

        fb = MelFilterbank(num_mels=num_mels, max_freq=1.0).get_filterbank()
        self.app_fb = ApplyFilterbank(fb)

        self.fft_length = fft_length
        self.hop_length = self.stft.hop_length
        self.num_mels = num_mels
        self.stretch_param = stretch_param

        self.counter = 0

    def forward(self, x, lengths=None):
        x = self.stft(x)

        if lengths is not None:
            lengths = _num_stft_bins(lengths, self.fft_length, self.hop_length, self.fft_length//2)

        if torch.rand(1)[0] <= self.prob and self.training:
            rate = 1 - self.dist.sample()
            x = self.pv(x, rate)
            lengths = (lengths.float()/rate).long()+1

        x = self.app_fb(self.cn(x))
        
        if self.norm is not None:
            x = self.norm(x)

        if lengths is not None:
            return x, lengths
        
        return x

    def __repr__(self):
        param_str = '(num_mels={}, fft_length={}, norm={}, stretch_param={})'.format(
                        self.num_mels, self.fft_length, self.norm.__name__, self.stretch_param)
        return self.__class__.__name__ + param_str