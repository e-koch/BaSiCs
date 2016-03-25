
import numpy as np
import warnings
from astropy.nddata.utils import extract_array, add_array
import astropy.units as u
from skimage.measure import EllipseModel, CircleModel, ransac
from cv2 import fitEllipse

from spectral_cube.lower_dimensional_structures import LowerDimensionalObject

from basics.utils import sig_clip
from basics.bubble_objects import Bubble2D
from basics.log import blob_log, _prune_blobs
from basics.bubble_edge import find_bubble_edges


eight_conn = np.ones((3, 3))


class BubbleFinder2D(object):
    """
    Image segmentation for bubbles in a 2D image.
    """
    def __init__(self, array, scales=None, threshold=None, channel=None,
                 mask=None, cut_to_box=False, pad_size=0, structure="beam",
                 beam=None, wcs=None):

        if isinstance(array, LowerDimensionalObject):
            self.array = array.value
            self.wcs = array.wcs

            if 'beam' in array.meta:
                self.beam = array.meta['beam']
            elif beam is not None:
                self.beam = beam
            else:
                raise KeyError("No 'beam' in metadata. Must manually specify "
                               "the beam with the beam keyword.")

        elif isinstance(array, np.ndarray):
            self.array = array

            if beam is not None:
                self.beam = beam
            else:
                raise KeyError("Must specify the beam with the beam keyword.")

            if wcs is not None:
                self.wcs = wcs
            else:
                raise KeyError("Must specify the wcs with the wcs keyword.")

        self._threshold = threshold
        self.mask = mask
        self.pad_size = pad_size

        self.array = np.nan_to_num(self.array)
        self._orig_shape = self.array.shape
        self.channel = channel

        pixscale = np.abs(self.wcs.pixel_scale_matrix[0, 0])
        fwhm_beam_pix = self.beam.major.value / pixscale
        beam_pix = np.ceil(fwhm_beam_pix / np.sqrt(8*np.log(2)))

        if scales is None:
            # Scales based on 2^n times the major beam radius
            # self.scales = beam_pix * 2 ** np.arange(0., 3.1)
            self.scales = beam_pix * np.arange(1., 8 + np.sqrt(2), np.sqrt(2))
        else:
            self.scales = scales

        # Default relative weightings for finding local maxima.
        self.weightings = np.ones_like(self.scales)
        # If searching at the beam size, decrease it's importance to
        # remove spurious features.
        if self.scales[0] == beam_pix:
            # 0.8 removes small spurious features in the IC1613 cube
            # Will need to run a proper noise test to better determine
            # what it should be set to
            self.weightings[0] = 0.8

        # When using non-gaussian like kernels, adjust the
        # widths to match the FWHM areas
        # self.tophat_scales = np.floor(self.scales * np.sqrt(2))

        self._atan_flag = False

    @property
    def mask(self):
        return self._mask

    @mask.setter
    def mask(self, mask_array):
        if mask_array is None:
            self._mask = np.ones_like(mask_array).astype(bool)
        else:
            if mask_array.shape != self.array.shape:
                raise TypeError("mask must match the shape of the given "
                                "array.")
            self._mask = mask_array

    @property
    def array(self):
        return self._array

    @array.setter
    def array(self, input_array):
        if input_array.ndim != 2:
            raise TypeError("Given array must be 2D.")

        self._array = input_array

    @property
    def pad_size(self):
        return self._pad_size

    @pad_size.setter
    def pad_size(self, value):
        if value < 0:
            raise ValueError("Pad size must be >=0")
        self._pad_size = value

    @property
    def threshold(self):
        return self._threshold

    @threshold.setter
    def threshold(self, value):

        if value is None:
            value = self.array.min()

        if not isinstance(value, u.Quantity):
            raise TypeError("Threshold must be an astropy Quantity.")

        if value.unit not in self.array.unit.find_equivalent_units():
            raise u.UnitsError("Threshold must have equivalent units"
                               " as the array " + str(self.array.unit))

        self._threshold = value

    def cut_to_bounding_box(self, pad_size=0):
        '''
        Reduce the array down to the minimum size based on the mask.
        Optionally add padding to the reduced size.
        '''

        if pad_size != self.pad_size:
            self.pad_size = pad_size

        mask_pixels = np.where(self.mask)

        yedges = [mask_pixels[0].min(), mask_pixels[0].max()]
        xedges = [mask_pixels[1].min(), mask_pixels[1].max()]

        self._center_coords = ((yedges[1] + 1 + yedges[0])/2,
                               (xedges[1] + 1 + xedges[0])/2)

        cut_shape = (yedges[1]+1-yedges[0], xedges[1]+1-xedges[0])

        cut_arr = extract_array(self.array, cut_shape, self.center_coords)
        cut_mask = extract_array(self.mask, cut_shape, self.center_coords)

        if self.pad_size > 0:
            # Pads edges with zeros
            self.array = np.pad(cut_arr, self.pad_size, mode='constant')
            self.mask = np.pad(cut_mask, self.pad_size, mode='constant')
        else:
            self.array = cut_arr
            self.mask = cut_mask

    @property
    def center_coords(self):
        return self._center_coords

    def insert_in_shape(self, shape):
        '''
        Insert the cut down mask into the given shape.
        '''

        if self.bubble_mask.shape == shape:
            return self.bubble_mask
        else:
            full_size = np.zeros(shape)
            return add_array(full_size, self.bubble_mask, self.center_coords)

    @property
    def bubble_mask(self):
        return self._bubble_mask

    def multiscale_bubblefind(self, scales=None, sigma=None, nsig=2,
                              overlap_frac=0.8, edge_find=True):
        '''
        Run find_bubbles on the specified scales.
        '''

        if scales is not None:
            self.scales = scales

        if sigma is None:
            sigma = sig_clip(self.array, nsig=10)

        self._regions = []
        all_props = []
        for i, props in enumerate(blob_log(self.array,
                                  sigma_list=self.scales,
                                  overlap=0.99,
                                  threshold=nsig*sigma,
                                  weighting=self.weightings)[0][::-1]):
            # print(i)
            # Adjust the region properties based on where the bubble edges are
            if edge_find:
                coords, shell_fraction, angular_std, mask = \
                    find_bubble_edges(self.array, props, max_extent=1.25,
                                      value_thresh=2*sigma,
                                      nsig_thresh=2, return_mask=True)
                # find_bubble_edges calculates the shell fraction
                # If it is below the given fraction, we skip the region.
                # print(len(coords))
                if len(coords) < 3:
                    print("Skipping %s" % (str(i)))
                    continue

                coords = np.array(coords)
                ymean = coords[:, 0].mean()
                xmean = coords[:, 1].mean()
                coords[:, 0] -= int(ymean)
                coords[:, 1] -= int(xmean)
                new_props = np.empty((5,))

                can_fit_ellipse = np.logical_and(shell_fraction > 0.5,
                                                 angular_std > 0.7)

                if len(coords) > 5 and can_fit_ellipse:
                    model = ransac(coords[:, ::-1], EllipseModel,
                                   max(5, int(0.1*len(coords))),
                                   props[2]/2.)[0]
                    pars = model.params.copy()
                    eccent = pars[2] / float(pars[3])
                    # Sometimes a < b??
                    if eccent < 1:
                        eccent = 1. / eccent
                        pars[2], pars[3] = pars[3], pars[2]
                        pars[4] = np.angle(np.exp(1j*(pars[4] + 0.5*np.pi)))
                    if pars[3] < 4.0 or pars[2] > 2.5*props[2] or eccent > 3.:
                        ellip_fail = True
                    else:
                        new_props[0] = pars[1] + int(ymean)
                        new_props[1] = pars[0] + int(xmean)
                        new_props[2] = pars[2]
                        new_props[3] = pars[3]
                        new_props[4] = pars[4]
                        ellip_fail = False
                else:
                    ellip_fail = True

                if ellip_fail:
                    model = ransac(coords[:, ::-1], CircleModel,
                                   max(3, int(0.1*len(coords))),
                                   props[2]/2.)[0]
                    if model.params[2] > 2.5*props[2]:
                        Warning("All fitting failed for: "+str(i))
                        continue
                    new_props[0] = model.params[1] + int(ymean)
                    new_props[1] = model.params[0] + int(xmean)
                    new_props[2] = model.params[2]
                    new_props[3] = model.params[2]
                    new_props[4] = 0.0

            new_coords, new_shell_fraction = \
                find_bubble_edges(self.array, new_props, max_extent=1.25,
                                  value_thresh=2*sigma,
                                  nsig_thresh=2, return_mask=False)[:2]
            print(shell_fraction)
            print(new_shell_fraction)

            # print("Shell fraction: ", shell_fraction)
            # print("Angular std: ", angular_std)
            # print(new_props)
            import matplotlib.pyplot as p
            ax = p.subplot(111)
            ax.plot(coords[:, 1]+xmean, coords[:, 0]+ymean, 'bo')
            ax.plot(new_coords[:, 1], new_coords[:, 0], 'go')
            ax.imshow(self.array, origin='lower', cmap='afmhot')
            bub = Bubble2D(props)
            ax.add_patch(bub.as_patch(color='b', fill=False, linewidth=2))
            bub = Bubble2D(new_props)
            ax.add_patch(bub.as_patch(color='g', fill=False, linewidth=2))
            p.draw()
            import time; time.sleep(0.1)
            raw_input("?")
            p.clf()

            new_props = np.append(new_props, shell_fraction)

            if self.channel is not None:
                new_props = np.append(new_props, self.channel)

            all_props.append(new_props)

        all_props = _prune_blobs(np.array(all_props), 0.9,
                                 use_shell_fraction=True)

        self._regions = [Bubble2D(props) for props in all_props]

    @property
    def regions(self):
        return self._regions

    @property
    def region_params(self):
        return np.array([bub2D.params for bub2D in self.regions])

    @property
    def num_regions(self):
        return len(self.regions)

    def region_rejection(self, value_thresh=0.0, grad_thresh=1,
                         frac_thresh=0.3, border_clear=True):
        '''
        2D bubble candidate rejection. Profile lines from the centre to edges
        of the should show a general increase in the intensity profile.
        Regions are removed when the fraction of sight lines without clear
        increases are below frac_thresh.
        '''

        # spec_shape = bubble_mask_cube.shape[0]

        # dy, dx = np.gradient(array, 9)
        # magnitude = np.sqrt(dy**2+dx**2)
        # orientation = np.arctan2(dy, dx)

        # grad_thresh = np.mean(magnitude) + grad_thresh * np.std(magnitude)

        if self.num_regions == 0:
            return

        rejected_regions = []

        for region in self.regions:

            region.find_shell_fraction(self.array, value_thresh=value_thresh,
                                       grad_thresh=grad_thresh)

            if region.shell_fraction < frac_thresh:
                rejected_regions.append(region)

        # Now remove
        self._regions = \
            list(set(self._regions) - set(rejected_regions))
