# Copyright 2023 Enzo Busseti
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""This module implements Estimator base classes.

Policies, costs, and constraints inherit from this.
"""

import numbers

import cvxpy as cp
import numpy as np
import pandas as pd

from .errors import DataError, MissingAssetsError, MissingTimesError, NaNError
from .utils import repr_numpy_pandas


class Estimator:
    """Estimator abstraction, designed for repeated evaluation over time.

    Policies, costs, and constraints inherit from this. When overloading
    methods defined here one should be very careful. The recommended usage
    (if you want a class that uses our recursive execution model) is to
    put any sub-estimators at the class attribute level, like we do
    throughout the library. That ensures that the sub-estimators will
    be evaluated before the class itself by both
    :method:`initialize_estimator_recursive` and
    :method:`values_in_time_recursive`.
    """

    # pylint: disable=useless-type-doc,useless-param-doc
    def initialize_estimator(self, universe, trading_calendar):
        """Initialize estimator instance with universe and trading times.

        This function is called whenever the trading universe changes.
        It provides the instance with the current trading universe and a
        :class:`pandas.DatetimeIndex` representing the current and future
        trading calendar, *i.e.*, the times at which the estimator will be
        evaluated. The instance uses these to appropriately initialize any
        internal object, such as Cvxpy parameters, to the right size (as
        implied by the universe). Also, especially for multi-period
        optimization and similar policies, awareness of the future trading
        calendar is essential to, *e.g.*, plan in advance.

        :param universe: Trading universe, including cash.
        :type universe: pandas.Index
        :param trading_calendar: Future (including current) trading calendar.
        :type trading_calendar: pandas.DatetimeIndex
        """

        # we don't raise NotImplementedError because this is called
        # on classes that don't re-define it

    def initialize_estimator_recursive(self, universe, trading_calendar):
        """Recursively initialize all estimators in a policy.

        :param universe: Names of assets to be traded.
        :type universe: pandas.Index
        :param trading_calendar: Times at which the estimator is
            expected to be evaluated.
        :type trading_calendar: pandas.DatetimeIndex
        """
        # pylint: disable=arguments-differ
        for _, subestimator in self.__dict__.items():
            if hasattr(subestimator, "initialize_estimator_recursive"):
                subestimator.initialize_estimator_recursive(
                    universe=universe, trading_calendar=trading_calendar)
        if hasattr(self, "initialize_estimator"):
            self.initialize_estimator(universe=universe,
                                      trading_calendar=trading_calendar)

    _current_value = None

    @property
    def current_value(self):
        """Current value of this instance.

        :returns: Current value, which can be any object.
        :rtype: numpy.array, pandas.Series, pandas.DataFrame, ...
        """
        return self._current_value

    def values_in_time_recursive(self, **kwargs):
        """Evaluate recursively on sub-estimators.

        This function is called by Simulator classes on Policy classes
        returning the current trades list. Policy classes, if they
        contain internal estimators, should declare them as attributes
        and call this base function (via `super()`) before they do their
        internal computation. CvxpyExpression estimators should instead
        define this method to update their Cvxpy parameters.

        Once we finalize the interface all parameters will be listed
        here.

        :param kwargs: Various parameters that are passed to all elements
            contained in a policy object.
        :type kwargs: dict

        :returns: The current value evaluated by this instance, if it
            implements the :meth:`values_in_time` method and it returns
            something there.
        :rtype: numpy.array, pandas.Series, pandas.DataFrame, ...
        """
        for _, subestimator in self.__dict__.items():
            if hasattr(subestimator, "values_in_time_recursive"):
                subestimator.values_in_time_recursive(**kwargs)
        if hasattr(self, "values_in_time"):
            self._current_value = self.values_in_time(**kwargs)
            return self.current_value
        return None

    def collect_hyperparameters(self):
        """Collect (recursively) all hyperparameters defined in a policy.

        :returns: List of :class:`cvxportfolio.hyperparameters.HyperParameter`
            instances.
        :rtype: list
        """
        result = []
        for _, subestimator in self.__dict__.items():
            if hasattr(subestimator, "collect_hyperparameters"):
                result += subestimator.collect_hyperparameters()
        return result

    def __repr__(self):
        """Pretty-print the cvxportfolio object in question.

        We make sure that every object the user interacts with can be
        pretty-printed to the interpreter, ideally in a way such that
        copy-pasting the output to the prompt results in an identical object.
        We can't do that all the times but we do our best. This is used
        throughout the library, for example it is included in backtest results
        so the user knows which policy generated that backtest, .... We prefer
        to define the logic of this directly insted of relying, e.g., on
        dataclasses logic, because we want to customize it to our usecase.
        """
        lhs = self.__class__.__name__ + '('
        core = ''
        for name, attr in self.__dict__.items():
            if attr is None:
                continue
            if hasattr(attr, "values_in_time_recursive") or\
                    hasattr(attr, "values_in_time") or (name[0] != '_'):
                core += name + '=' + attr.__repr__() + ', '
        core = core[:-2]  # remove trailing comma and space if present
        rhs = ')'
        return lhs + core + rhs


class CvxpyExpressionEstimator(Estimator):
    """Base class for estimators that are Cvxpy expressions."""

    def compile_to_cvxpy(self, w_plus, z, w_plus_minus_w_bm):
        """Compile term to cvxpy expression.

        This is called by a Policy class on its terms before the start
        of the backtest to compile its Cvxpy problem. If the Policy
        changes in time this is called at every time step.

        It can either return a scalar expression, in the case of
        objective terms, or a list of cvxpy constraints, in the case of
        constraints.

        In MultiPeriodOptimization policies this is called separately
        for costs and constraints at different look-ahead steps with the
        corresponding w_plus and z.

        :param w_plus: Post-trade weights.
        :type w_plus: cvxpy.Variable
        :param z: Trade weights.
        :type z: cvxpy.Variable
        :param w_plus_minus_w_bm: Post-trade weights minus benchmark
            weights.
        :type w_plus_minus_w_bm: cvxpy.Variable
        """
        raise NotImplementedError


class DataEstimator(Estimator):
    """Estimator of point-in-time values from internal `self.data`.

    It also implements logic to check that no `np.nan` are returned
    by its `values_in_time_recursive` method, which is the way `cvxportfolio`
    objects use this class to get data, to compile and update a Cvxpy
    parameter, and to slice the data with the current trading universe.

    :param data: Data expressed preferably as pandas Series or DataFrame
        where the first index is a pandas.DateTimeIndex. Otherwise you can
        pass a callable object which implements the values_in_time_recursive
        method (with the standard signature) and returns the corresponding
        value in time, or a constant float, numpy.array, or even pandas Series
        or DataFrame not indexed by time (e.g., a covariance matrix where both
        index and columns are the stock symbols).
    :type data: object, pandas.Series, pandas.DataFrame
    :param use_last_available_time: if the pandas index exists
        and is a pandas.DateTimeIndex you can instruct
        :method:`values_in_time_recursive` to retrieve the last available value
        at time t by setting this to True. Default is False.
    :type use_last_available_time: bool
    :param allow_nans: If True, allow data returned to contain `numpy.nan`s.
        Default False.
    :type allow_nans: bool
    :param compile_parameter: If True, compile a Cvxpy parameter that gets
        updated with the current value of the instance at each point in a
        backtest. Default False.
    :type compile_parameter: bool
    :param non_negative: If True, the compiled Cvxpy parameter is non-negative
        (this affects certain Cvxpy operations). Default False
    :type non_negative: bool
    :param positive_semi_definite: If True, the compiled Cvxpy parameter is
        market as a positive semi-definite matrix (this affects certain Cvxpy
        operations). Default False.
    :type positive_semi_definite: bool
    :param data_includes_cash: If True, when the data is sliced with the
        current trading universe we also look for the values corresponding to
        the cash account. Default False.
    :type data_includes_cash: bool
    :param ignore_shape_check: If True, we don't do any slicing of the data
        according to the current trading universe. Default False
    :type ignore_shape_check: bool

    :raises cvxportfolio.NaNError: If np.nan's are present in result.
    :raises cvxportfolio.MissingTimesError: If some times are missing.
    :raises cvxportfolio.MissingAssetsError: If some assets are missing.
    :raises cvxportfolio.DataError: If data is not in the right form.
    """

    def __init__(
            self, data, use_last_available_time=False, allow_nans=False,
            compile_parameter=False, non_negative=False,
            positive_semi_definite=False, data_includes_cash=False,
            ignore_shape_check=False):
        self.data = data
        self._use_last_available_time = use_last_available_time
        self._allow_nans = allow_nans
        self._compile_parameter = compile_parameter
        self._non_negative = non_negative
        self._positive_semi_definite = positive_semi_definite
        self._universe_maybe_noncash = None
        self._data_includes_cash = data_includes_cash
        self._ignore_shape_check = ignore_shape_check
        self.parameter = None

    def initialize_estimator(self, universe, trading_calendar):
        """Initialize with current universe.

        :param universe: Trading universe, including cash.
        :type universe: pandas.Index
        :param trading_calendar: Future (including current) trading calendar.
        :type trading_calendar: pandas.DatetimeIndex
        """

        self._universe_maybe_noncash = \
            universe if self._data_includes_cash else universe[:-1]

        if self._compile_parameter:
            value = self._internal_values_in_time(
                t=trading_calendar[0])
            self.parameter = cp.Parameter(
                value.shape if hasattr(value, "shape") else (),
                PSD=self._positive_semi_definite, nonneg=self._non_negative)

    def value_checker(self, result):
        """Ensure that only scalars or arrays without np.nan are returned.

        :raises cvxportfolio.errors.NaNError: If NaNs are found.
        :raises cvxportfolio.errors.DataError: If the value passed is not a
            scalar or Numpy array.

        :param result: Scalar or array that we check has no NaNs.
        :type result: float or numpy.array

        :returns: Input value; if array, a copy.
        :rtype: float or numpy.array
        """

        if isinstance(result, numbers.Number):
            if np.isnan(result) and not self._allow_nans:
                raise NaNError(
                    f"{self.__class__.__name__}.values_in_time_recursive"
                    + " result is a np.nan scalar.")
            return result

        if isinstance(result, np.ndarray):
            if np.any(np.isnan(result)) and not self._allow_nans:
                message = f"{self.__class__.__name__}.values_in_time_recursive"
                message += " result is an array with np.nan's."
                raise NaNError(message)
            # we pass a copy because it can be accidentally overwritten
            return np.array(result)

        raise DataError(
            f"{self.__class__.__name__}.values_in_time_recursive result"
            + " is not a scalar or array.")

    def _universe_subselect(self, data):
        """This function subselects from ``data`` the relevant universe.

        See github issue #106.

        If data is a pandas Series we subselect its index. If we fail we
        throw an error. If data is a pandas DataFrame (covariance,
        exposure matrix) we try to subselect its index and columns. If
        we fail on either we ignore the failure, but if we fail on both
        we throw an error. If data is a numpy 1-d array we check that
        its length is the same as the universe's. If it is a 2-d array
        we check that at least one dimension is the same as the
        universe's. If the universe is None we skip all checks. (We may
        revisit this choice.) This only happens if the DataEstimator
        instance is not part of a Estimator tree (a usecase which we
        will probably drop).
        """

        if (self._universe_maybe_noncash is None) or self._ignore_shape_check:
            return data.values if hasattr(data, 'values') else data

        if isinstance(data, pd.Series):
            try:
                return data.loc[self._universe_maybe_noncash].values
            except KeyError as exc:
                raise MissingAssetsError(
                    "The pandas Series found by %s has index %s"
                    + " while the current universe%s"
                    + " is %s. It was not possible to reconcile the two.",
                    self.__class__.__name__, data.index,
                    ' minus cash' if not self._data_includes_cash else ' ',
                    self._universe_maybe_noncash) from exc

        if isinstance(data, pd.DataFrame):
            try:
                return data.loc[self._universe_maybe_noncash,
                    self._universe_maybe_noncash].values
            except KeyError:
                try:
                    return data.loc[:, self._universe_maybe_noncash].values
                except KeyError:
                    try:
                        return data.loc[self._universe_maybe_noncash, :].values
                    except KeyError:
                        pass
            raise MissingAssetsError(
                "The pandas DataFrame found by %s has index %s"
                + " and columns %s"
                + " while the current universe%s"
                + " is %s. It was not possible to reconcile the two.",
                self.__class__.__name__, data.columns,
                ' minus cash' if not self._data_includes_cash else ' ',
                self._universe_maybe_noncash)

        if isinstance(data, np.ndarray):
            dimensions = data.shape
            if not len(self._universe_maybe_noncash) in dimensions:
                raise MissingAssetsError(
                    "The numpy array found by %s has dimensions %s"
                    + " while the current universe%s "
                    + "has size %s. It was not possible to reconcile the two.",
                    self.__class__.__name__, data.shape,
                    ' minus cash' if not self._data_includes_cash else ' ',
                    len(self._universe_maybe_noncash))
            return data

        # scalar
        return data

    def _internal_values_in_time(self, t, **kwargs):
        """Internal method called by :method:`values_in_time`."""

        # here we trust the result (change?)
        if hasattr(self.data, "values_in_time_recursive"):
            return self.data.current_value

        # here (probably user-provided) we check
        if hasattr(self.data, "values_in_time"):
            return self.value_checker(self._universe_subselect(
                self.data.current_value if hasattr(self.data, 'current_value')
                else self.data.values_in_time(t=t, **kwargs)))

        # if self.data is pandas and has datetime (first) index
        if (hasattr(self.data, "loc") and hasattr(self.data, "index")
            and (isinstance(self.data.index, pd.DatetimeIndex)
                 or (isinstance(self.data.index, pd.MultiIndex) and
                     isinstance(self.data.index.levels[0],
                         pd.DatetimeIndex)))):
            try:
                if self._use_last_available_time:
                    if isinstance(self.data.index, pd.MultiIndex):
                        newt = self.data.index.levels[0][
                            self.data.index.levels[0] <= t][-1]
                    else:
                        newt = self.data.index[self.data.index <= t][-1]
                    tmp = self.data.loc[newt]
                else:
                    tmp = self.data.loc[t]

                return self.value_checker(self._universe_subselect(tmp))

            except (KeyError, IndexError) as exc:
                raise MissingTimesError(
                    "%s.values_in_time_recursive could not find data"
                    + " for time %s. This could be due to wrong timezone"
                    + " setting: in general Cvxportfolio objects are timezone"
                    + " aware, the data you pass should be as well.",
                     self, t) from exc

        # if data is pandas but no datetime index (constant in time)
        if hasattr(self.data, "values"):
            return self.value_checker(self._universe_subselect(self.data))

        # if data is scalar or numpy
        return self.value_checker(self._universe_subselect(self.data))

    def values_in_time(self, **kwargs):
        """Obtain value of `self.data` at time t or right before.

        :param kwargs: All parameters passed to :meth:`values_in_time`.
        :type kwargs: dict

        :raises cvxportfolio.errors.NaNError: The data provided contains
            NaNs at current time.

        :returns: The  value from this
            :class:`cvxportfolio.estimator.DataEstimator` at current time.
        :rtype: int, float, numpy.ndarray
        """
        try:
            result = self._internal_values_in_time(**kwargs)
        except NaNError as exc:
            raise NaNError(f"{self.__class__.__name__} found NaNs"
                + f" at time {kwargs['t']}.") from exc
        if self.parameter is not None:
            self.parameter.value = result
        return result

    def __repr__(self):
        """Pretty-print."""
        if np.isscalar(self.data):
            return str(self.data)
        if hasattr(self.data, 'values_in_time_recursive'
            ) or hasattr(self.data, 'values_in_time'):
            return self.data.__repr__()
        return repr_numpy_pandas(self.data)
