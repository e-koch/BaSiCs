
import numpy as np
from astropy.table import Table, Column
import astropy.units as u
from astropy.coordinates import SkyCoord
import matplotlib.pyplot as p
from warnings import warn

from galaxy_utils import gal_props_checker


all_columns = ["pa", "bubble_type", "velocity_center", "velocity_width",
               "eccentricity", "expansion_velocity", "avg_shell_flux_density",
               "total_shell_flux_density", "shell_column_density",
               "hole_contrast", "diameter_physical", "major_physical",
               "minor_physical", "diameter_angular", "major_angular",
               "minor_angular", "galactic_radius", "galactic_pa",
               "tkin", "shell_volume_density", "volume", "hole_mass",
               "formation_energy", "shell_fraction", "is_closed"]

not_numerical = ["col0"]  # Once finished, this will be center_coord


def _has_nan(values, name):
    if np.isnan(np.array(values)).any():
        # raise ValueError("NaN in {}".format(name))
        warn("NaN in {}".format(name))
    return values


class PP_Catalog(object):
    """docstring for PP_Catalog"""
    def __init__(self, bubbles):
        super(PP_Catalog, self).__init__()
        self.bubbles = bubbles


class PPV_Catalog(object):
    """
    Return a table structure of the bubble properties and make it easy to
    visualize the populations.

    Parameters
    ----------
    bubbles : list of Bubble3D objects or astropy.table.Table
        Bubble3D objects.
    """
    def __init__(self, bubbles, galaxy_props=None):
        super(PPV_Catalog, self).__init__()

        if isinstance(bubbles, list):
            if galaxy_props is None:
                raise TypeError("Galaxy properties must be given when bubbles"
                                " is a list of bubble objects.")
            self.create_table(bubbles, galaxy_props)

        elif isinstance(bubbles, Table):
            self.table = bubbles
        else:
            raise TypeError("bubbles must be a list of bubble objects or a "
                            "pre-made astropy table.")

    @staticmethod
    def from_file(filename, format='ascii.ecsv'):

        try:
            tab = Table.read(filename, format=format)
        except Exception as e:
            # Add something a more helpful here.
            raise e

        self = PPV_Catalog(tab)

        return self

    def create_table(self, bubbles, galaxy_props):
        '''
        Create a Table from a list of bubbles
        '''

        # Check to make sure all of the properties are there
        gal_props_checker(galaxy_props)

        # Create columns of the bubble properties
        props = {"pa": [u.deg, "Position angle of the bubble"],
                 "bubble_type": [u.dimensionless_unscaled, "Type of bubble"],
                 "velocity_center": [u.km / u.s, "Center velocity"],
                 "velocity_width": [u.km / u.s, "Range of velocities bubble"
                                                " is detected."],
                 "eccentricity": [u.dimensionless_unscaled,
                                  "Shape eccentricity"],
                 "expansion_velocity": [u.km / u.s, "Expansion velocity"],
                 "avg_shell_flux_density": [u.K * u.km / u.s,
                                            "Average flux density in bubble "
                                            "shell"],
                 "total_shell_flux_density": [u.K * u.km / u.s,
                                              "Total flux density in bubble "
                                              "shell"],
                 "shell_column_density": [u.cm ** -2,
                                          "Average column density in the "
                                          "shell"],
                 "hole_contrast": [u.dimensionless_unscaled,
                                   "Average intensity difference between hole"
                                   " and shell."],
                 "diameter_physical": [u.pc, "Physical diameter"],
                 "major_physical": [u.pc, "Physical major radius"],
                 "minor_physical": [u.pc, "Physical minor radius"],
                 "diameter_angular": [u.deg, "Angular diameter"],
                 "major_angular": [u.deg, "Angular major radius"],
                 "minor_angular": [u.deg, "Angular minor radius"],
                 "galactic_radius": [u.kpc, "Galactic radius of the"
                                            " center."],
                 "galactic_pa": [u.deg, "Galactic PA of the center."],
                 "shell_fraction": [u.dimensionless_unscaled,
                                    "Fraction of shell enclosing the hole."],
                 "is_closed": [u.dimensionless_unscaled, "Closed or partial "
                               "shell (shell fraction > 0.9 is closed)"]}

        prop_funcs = {"tkin": [u.Myr, "Kinetic age of the bubble.", {}],
                      "shell_volume_density":
                      [u.cm ** -3,
                       "Average hydrogen volume "
                       "density in the shell.",
                       {"scale_height": galaxy_props["scale_height"],
                        "inclination": galaxy_props["inclination"]}],
                      "volume":
                      [u.pc ** 3, "Volume of the hole.",
                       {"scale_height": galaxy_props["scale_height"]}],
                      "hole_mass":
                      [u.Msun, "Inferred mass of the hole from the shell"
                               " volume density.",
                       {"scale_height": galaxy_props["scale_height"],
                        "inclination": galaxy_props["inclination"]}],
                      "formation_energy":
                      [u.erg, "Energy required to create the hole.",
                       {"scale_height": galaxy_props["scale_height"],
                        "inclination": galaxy_props["inclination"]}]}

        columns = []

        # The center coordinates are different, since they're SkyCoords
        columns.append(SkyCoord([bub.center_coordinate for bub in bubbles]))

        # Same for is_closed
        columns.append(Column([bub.is_closed for bub in bubbles],
                              unit=u.dimensionless_unscaled,
                              description="Closed or partial shell.",
                              name="closed_shell"))

        # Add the properties
        for name in props:
            unit, descrip = props[name]
            columns.append(Column(_has_nan([getattr(bub, name).to(unit).value
                                            for bub in bubbles], name),
                                  name=name, description=descrip,
                                  unit=unit.to_string()))

        # Add the functions
        for name in prop_funcs:
            unit, descrip, imps = prop_funcs[name]
            columns.append(
                Column(_has_nan([getattr(bub, name)(**imps).to(unit).value
                                 for bub in bubbles], name), name=name,
                       description=descrip,
                       unit=unit.to_string()))

        # all_names = ["center_coordinate"] + props.keys() + prop_funcs.keys()

        self.table = Table(columns)

    def _check_given_column(self, name):
        if name not in all_columns:
            raise ValueError("{} is not a defined column name.".format(name))

    def population_statistics(self, column, percentiles=[25, 50, 75]):
        '''
        Return percentiles of properties in the population
        '''
        self._check_given_column(column)

        # There really should never be a NaN in the table...
        return np.percentile(self.table[column], percentiles) * \
            self.table[column].unit

    def histogram(self, column, log_scale=False, **kwargs):
        self._check_given_column(column)

        if log_scale:
            p.hist(np.log10(self.table[column]), **kwargs)

            label = "log({0} / {1})".format(self.table[column].name,
                                            self.table[column].unit)
        else:
            p.hist(self.table[column], **kwargs)

            label = "{0} ({1})".format(self.table[column].name,
                                       self.table[column].unit)
        p.xlabel(label)

    def scatter(self, xcolumn, ycolumn, xlog_scale=False, ylog_scale=False,
                **kwargs):

        self._check_given_column(xcolumn)
        self._check_given_column(ycolumn)

        if xlog_scale:
            xdata = np.log10(self.table[xcolumn])
            xlabel = "log({0} / {1})".format(self.table[xcolumn].name,
                                             self.table[xcolumn].unit)
        else:
            xdata = self.table[xcolumn]
            xlabel = "{0} ({1})".format(self.table[xcolumn].name,
                                        self.table[xcolumn].unit)

        if ylog_scale:
            ydata = np.log10(self.table[ycolumn])
            ylabel = "log({0} / {1})".format(self.table[ycolumn].name,
                                             self.table[ycolumn].unit)
        else:
            ydata = self.table[ycolumn]
            ylabel = "{0} ({1})".format(self.table[ycolumn].name,
                                        self.table[ycolumn].unit)

        p.scatter(xdata, ydata, **kwargs)

        p.ylabel(ylabel)
        p.xlabel(xlabel)

    def corner_plot(self, columns, log_scale=None, **kwargs):
        '''
        Parameters
        ----------
        columns : list of columns names
            Columns to include in the cornerplot.
        '''

        for column in columns:
            if column in not_numerical:
                raise ValueError("{} is not numerical. Cannot "
                                 "plot.".format(column))
            self._check_given_column(column)

        try:
            from corner import corner
        except ImportError:
            raise ImportError("The corner package must be installed. "
                              " (pip install corner)")

        labels = ["{0} ({1})".format(self.table[column].name,
                                     self.table[column].unit) for
                  column in columns]

        if log_scale is None:
            log_scale = np.array([False] * len(columns))
        else:
            if len(log_scale) != len(columns):
                raise IndexError("log_scale must have the same length as"
                                 " columns.")

        data = []

        for name, app_log in zip(columns, log_scale):
            if app_log:
                data.append(np.log10(self.table[name].astype(np.float)))
            else:
                data.append(self.table[name].astype(np.float))

        data = np.array([self.table[name].astype(np.float)
                         for name in columns]).T

        corner(data, labels=labels,
               truths=[0.0, 0.0, 0.0],
               quantiles=[0.16, 0.5, 0.84],
               show_titles=True, title_kwargs={"fontsize": 12})

    def write_table(self, filename, format='ascii.ecsv'):
        '''
        Write the table. Format must be supported by astropy.table.
        '''
        self.table.write(filename, format=format)
