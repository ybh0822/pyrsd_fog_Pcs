from pyRSD.rsdfit.parameters import Parameter, ParameterSet
from pyRSD.rsd._cache import Property
from pyRSD.rsd.transfers import WindowFunctionTransfer, gridded_transfers
from pyRSD.rsdfit.theory import decorators

from scipy.interpolate import InterpolatedUnivariateSpline as spline
import numpy as np
import xarray as xr
from six import string_types
import contextlib
import functools
import warnings

def deprecated_parameter(func):
    """
    This is a decorator which can be used to mark parameters
    as deprecated
    """
    @functools.wraps(func)
    def new_func(*args, **kwargs):
        warnings.simplefilter('always', DeprecationWarning) #turn off filter
        warnings.warn("The model parameter is '%s' is deprecated" %func.__name__, category=DeprecationWarning, stacklevel=2)
        warnings.simplefilter('default', DeprecationWarning) #reset filter
        return func(*args, **kwargs)

    return new_func

class Schema(type):
    """
    Metaclass to gather all `free` and `fixed`
    attributes from the class
    """
    def __init__(cls, clsname, bases, attrs):

        # attach the registry attributes
        cls._free = set()
        cls._fixed = set()
        cls._model_params = set()
        cls._extra_params = set()
        cls._deprecated = set()

        # loop over each attribute
        for name in cls.__dict__:
            p = cls.__dict__[name]
            if isinstance(p, FreeProperty):
                if not p.deprecated:
                    cls._free.add(name)
                    if p.model_param: cls._model_params.add(name)
                    else: cls._extra_params.add(name)
                else:
                    cls._deprecated.add(name)
            elif isinstance(p, FixedProperty):
                if not p.deprecated:
                    cls._fixed.add(name)
                    if p.model_param: cls._model_params.add(name)
                    else: cls._extra_params.add(name)
                else:
                    cls._deprecated.add(name)


class FreeProperty(Property):
    """
    A free property
    """
    pass

class FixedProperty(Property):
    """
    A fixed property
    """
    pass


def free(model_param=True, deprecated=False):
    """
    Decorator to represent a freely varying model parameter
    """
    def dec(f):
        name = f.__name__
        _name = '__'+name

        @functools.wraps(f)
        def _get_property(self):
            val = f(self)
            val['vary'] = True
            val['name'] = name
            val['description'] = f.__doc__.strip()
            return Parameter(**val)

        if deprecated: _get_property = deprecated_parameter(_get_property)
        prop = FreeProperty(_get_property)
        prop.model_param = model_param
        prop.deprecated = deprecated
        return prop
    return dec

def fixed(model_param=False, deprecated=False):
    """
    Decorator to represent a model parameter, either fixed
    or free
    """
    def dec(f):
        name = f.__name__
        _name = '__'+name

        @functools.wraps(f)
        def _get_property(self):
            val = f(self)
            val['vary'] = False
            val['name'] = name
            val['description'] = f.__doc__.strip()
            return Parameter(**val)

        if deprecated: _get_property = deprecated_parameter(_get_property)
        prop = FixedProperty(_get_property)
        prop.model_param = model_param
        prop.deprecated = deprecated
        return prop
    return dec


class BasePowerParameters(ParameterSet):
    """
    A base `ParameterSet` class to represent parameters of a RSD model
    """
    defaults = None
    _model_cls = None

    def to_file(self, filename, mode='w'):
        """
        Output the theory `ParameterSet` to a file, using the mode specified.

        Parameters
        ----------
        filename : str
            the name of the file to write
        mode : str, optional
            the file mode, i.e., 'w' to write, 'a' to append
        """
        kwargs = {'header_name':'theory params', 'prefix':'theory', 'footer':True, 'as_dict':True}
        ParameterSet.to_file(self, filename, mode=mode, **kwargs)

    @classmethod
    def from_defaults(cls, model=None, extra_params=[]):
        """
        Initialize from a default set of parameters

        Parameters
        ----------
        model : GalaxySpectrum, optional
            the model instance; if not provided, a new model
            will be initialized
        extra_params : list, optional
            list of names of extra parameters to be treated as valid
        """
        # initialize an empty class
        params = cls()

        # add extra parameters
        params.extra_params = []
        params.extra_params.extend(extra_params)

        # get the model
        if model is None:
            model = cls._model_cls()
        elif not isinstance(model, cls._model_cls):
            name = cls._model_cls.__name__
            raise TypeError("model should be a ``%s``" %name)

        # set the model
        params.model = model

        # delay asteval until all our loaded
        with params.delayed_asteval():

            # add the parameters
            params.add_many(*[par for par in cls.defaults])

            # first add this to the symtable, before updating constraints
            params.register_function('sigmav_from_bias', params.model.sigmav_from_bias)

        # update constraints
        params.prepare_params()
        params.update_constraints()

        # set the tag to "theory"
        params.tag = "theory"

        return params

    @classmethod
    def from_file(cls, filename, model=None, tag=[], extra_params=[]):
        """
        Parameters
        ----------
        filename : str
            the name of the file to read the parameters from
        tag : str, optional
            only read the parameters with this label tag
        extra_params : list, optional
            a list of any additional parameters to treat as valid
        """
        # get the defaults first
        params = cls.from_defaults(model=model, extra_params=extra_params)

        # update descriptions
        with params.delayed_asteval():

            # update with values from file
            fromfile = super(BasePowerParameters, cls).from_file(filename, tags=tag)
            params.tag = fromfile.tag # transfer the tag
            for name in fromfile:

                # ignore deprecated parameters
                if name not in cls.defaults.deprecated:
                    params.add(**fromfile[name].to_dict())

            # first add this to the symtable, before updating constraints
            params.register_function('sigmav_from_bias', params.model.sigmav_from_bias)


        # update constraints
        params.prepare_params()
        params.update_constraints()

        # check for any fixed, constrained values
        for name in params:
            p = params[name]

            # if no dependencies are free, set vary = False, constrained = False
            if p.constrained:
                if not any(params[dep].vary for dep in p.deps):
                    p.vary = False
                    p.constrained = False

        return params

    @property
    def valid_model_params(self):
        """
        A list of the valid parameters names that can be passed
        to the ``GalaxySpectrum`` model instance
        """
        return self.defaults.model_params

    def __setitem__(self, name, value):
        """
        Only allow names to be set if they are a valid parameter, and
        if not, crash
        """
        if not self.is_valid(name):
            raise RuntimeError("`%s` is not a valid parameter name" %name)
        ParameterSet.__setitem__(self, name, value)

    def is_valid(self, name):
        """
        Check if the parameter name is valid
        """
        extras = getattr(self, 'extra_params', [])
        return name in self.defaults or name in self.defaults.deprecated or name in extras

    def to_dict(self):
        """
        Return a dictionary of (name, value) for each name that is in
        :attr:`model_params`
        """
        return dict((key, self[key].value) for key in self.valid_model_params if key in self)

    def set_free_parameters(self, theta):
        """
        Given an array of values `theta`, set the free parameters of
        `GalaxyPowerTheory.fit_params`

        Notes
        -----
        * if any free parameter values are outside their bounds, the
        model will not be updated and `False` will be returned

        Returns
        -------
        valid_model : bool
            return `True/False` flag indicating if we were able
            to successfully set the free parameters and update the model
        """
        # only set and update the model when all free params are
        # within max/min bounds and uniform prior bounds
        if not all(p.within_bounds(theta[i]) for i,p in enumerate(self.free)):
            return False

        # try to update
        try:
            self.update_values(**dict(zip(self.free_names, theta)))
        except Exception as e:
            import traceback
            msg = "exception while trying to update free parameters:\n"
            msg += "   current parameters:\n%s\n" %str(self.fit_params)
            msg += "   traceback:\n%s" %(traceback.format_exc())
            raise RuntimeError(msg)
        try:
            self.model.update(**self.to_dict())
        except Exception as e:
            import traceback
            msg = "exception while trying to update the theoretical model:\n"
            msg += "   current parameters:\n%s\n" %str(self)
            msg += "   traceback:\n%s" %(traceback.format_exc())
            raise RuntimeError(msg)

        return True

    def check(self, return_errors=False):
        """
        Check the values of all parameters. Here, `check` means that
        each parameter is within its bounds and the prior is not infinity

        If `return_errors = True`, return the error messages as well
        """
        error_messages = []
        doing_okay = True

        # loop over each parameter
        for name in self.free_names:
            par = self[name]

            # check bounds
            if par.bounded and not par.within_bounds():
                doing_okay = False
                args = par.name, par.value, par.min, par.max
                msg = '{}={} is outside of reasonable limits [{}, {}]'.format(
                    *args)
                error_messages.append(msg)
                continue

            # check prior
            if par.has_prior and np.isinf(par.lnprior):
                doing_okay = False
                msg = '{}={} is outside of prior {}'.format(
                    par.name, par.value, par.prior)
                error_messages.append(msg)
                continue

        if return_errors:
            return doing_okay, error_messages
        else:
            return doing_okay

    def scale(self, theta):
        """
        Scale the (unscaled) free parameters, using the priors to
        define the scaling transformation
        """
        return (theta - self.locs) / self.scales

    def inverse_scale(self, theta):
        """
        Inverse scale the free parameters, using the priors to
        define the scaling transformation
        """
        return theta*self.scales + self.locs

    def scale_gradient(self, grad):
        """
        Scale the gradient with respect to the unscaled free parameters,
        using the priors to define the scaling transformation

        This returns df / dxprime where xprime is the scaled param vector
        """
        return grad * self.scales

class BasePowerTheory(object):
    """
    A base class representing a theory for computing a redshift-space power
    spectrum.


    It handles the dependencies between model parameters and the
    evaluation of the model itself.
    """
    def __init__(self, model_cls, theory_cls, param_file,
                    model=None, extra_param_file=None, kmin=None, kmax=None):
        """
        Parameters
        ----------
        param_file : str
            name of the file holding the parameters for the theory
        extra_param_file : str
            name of the file holding the names of any extra parameter files
        model : subclass of , optional
            the model instance; if not provided, a new model
            will be initialized
        kmin : float, optional
            If not `None`, initalize the model with this `kmin` value
        kmax : float, optional
            If not `None`, initalize the model with this `kmax` value
        """
        # read in the parameters again to get params that aren't fit params
        self.model_params = ParameterSet.from_file(param_file, tags='model')

        # now setup the model parameters; only the valid model kwargs are read
        allowable_model_params = model_cls.allowable_kwargs
        for param in list(self.model_params.keys()):
            if param not in allowable_model_params and param != '__version__':
                del self.model_params[param]

        # store the kmin, kmax (used when setting model)
        self.kmin, self.kmax = kmin, kmax

        # set the model
        self._model_cls = model_cls
        self.model = model

        # read the parameter file lines and save them for pickling
        self._readlines = open(param_file, 'r').readlines()

        # read any extra parameters and make a dict
        self.extra_params = []
        if extra_param_file is not None:
            extra_params =  ParameterSet.from_file(extra_param_file)
            self.extra_params = extra_params.keys()

        # try to also read any extra params from the param file, tagged with 'theory_extra'
        extra_params = ParameterSet.from_file(param_file, tags='theory_extra')
        if len(extra_params):
            self.extra_params.extend(extra_params.keys())

        # read in the fit parameters; this should read only the keys that
        # are valid for the GalaxyPowerParameters
        kwargs = {'tag':'theory', 'model':self.model, 'extra_params':self.extra_params}
        self.fit_params = theory_cls.from_file(param_file, **kwargs)

        # delete any empty parameters
        for k in list(self.fit_params):
            if self.fit_params[k].value is None:
                del self.fit_params[k]

    @contextlib.contextmanager
    def preserve(self, theta):
        """
        Context manager that preserves the state of the model
        upon exiting the context by first saving and then restoring it
        """
        # save the free values
        original_state = self.free_values

        # set the input state
        for i, name in enumerate(self.free_names):
            self.fit_params[name].value = theta[i]

        yield

        # restore old state
        old = dict(zip(self.free_names, original_state))
        self.fit_params.update_values(**old)

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        """
        Set the model, possibly from a file, or initialize a new one
        """
        from ..util import rsd_io

        # model kwargs
        kwargs = {k:v() for k,v in self.model_params.items()}
        if self.kmin is not None:
            if self.kmin < kwargs.get('kmin', np.inf):
                kwargs['kmin'] = self.kmin
        if self.kmax is not None:
            if self.kmax > kwargs.get('kmax', -np.inf):
                kwargs['kmax'] = self.kmax

        if value is None:
            self._model = self._model_cls(**kwargs)
        elif isinstance(value, string_types):
            self._model = rsd_io.load_model(value, show_warning=False)
            self._model.update(**kwargs)
        elif isinstance(value, self._model_cls):
            self._model = value
            self._model.update(**kwargs)
        else:
            raise rsd_io.ConfigurationError("failure to set model in BasePowerTheory from file or instance")

        if not isinstance(self._model, self._model_cls):
            bad = self._model.__class__.__name__
            good = self._model_cls.__name__
            raise ValueError("model class is %s, but should be %s" % (bad, good))

        # set the fit params model too
        if hasattr(self, 'fit_params'):
            self.fit_params.model = self._model

    def to_file(self, filename, mode='w'):
        """
        Save the parameters of this theory in a file
        """
        # first save the fit params
        self.fit_params.to_file(filename, mode='a')

        # now any extra params
        if self.extra_params is not None:
            f = open(filename, 'a')
            vals = []
            for name in self.extra_params:
                if name in self.fit_params:
                    desc = self.fit_params[name].description
                    vals.append("theory_extra.%s =  '%s'" %(name, desc))
            f.write("%s\n\n" %("\n".join(vals)))
            f.close()

        # now save the model params
        kwargs = {'mode':'a', 'header_name':'model params', 'footer':True, 'as_dict':False}
        self.model_params.to_file(filename, **kwargs)

    #---------------------------------------------------------------------------
    # properties
    #---------------------------------------------------------------------------
    @property
    def ndim(self):
        """
        Returns the number of free parameters, i.e., the `dimension` of the
        theory
        """
        return len(self.free_names)

    @property
    def lnprior_free(self):
        """
        Return the log prior for free parameters as the sum of the priors
        of each individual parameter
        """
        return sum(param.lnprior for param in self.free)

    @property
    def lnprior_constrained(self):
        """
        Return the log prior for constrained parameters as the sum of the priors
        of each individual parameter
        """
        return sum(param.lnprior for param in self.constrained)

    @property
    def lnprior(self):
        """
        Return the log prior for all "free" parameters as the sum of the priors
        of each individual parameter
        """
        return self.lnprior_free

    @property
    def dlnprior(self):
        """
        Return the derivative of the log prior for all "free" parameters
        """
        return np.array([p.dlnprior for p in self.free])

    #---------------------------------------------------------------------------
    # convenience attributes
    #---------------------------------------------------------------------------
    @property
    def free_fiducial(self):
        """
        Return an array of the fiducial free parameters
        """
        free = self.free_names
        params = self.fit_params
        toret = [params[key].fiducial for key in free]
        if None in toret:
            names = [free[i] for i in range(len(free)) if toret[i] is None]
            raise ValueError("fiducial values missing for parameters: %s" %str(names))
        return np.array(toret)

    @property
    def free_names(self):
        return self.fit_params.free_names

    @property
    def free_values(self):
        return self.fit_params.free_values

    @property
    def free(self):
        return self.fit_params.free

    @property
    def constrained_names(self):
        return self.fit_params.constrained_names

    @property
    def constrained_values(self):
        return self.fit_params.constrained_values

    @property
    def constrained(self):
        return self.fit_params.constrained

    @property
    def pkmu_gradient(self):
        """
        Return the P(k,mu) gradient class
        """
        try:
            return self._pkmu_gradient
        except AttributeError:
            self._pkmu_gradient = self.model.get_gradient(self.fit_params)
            return self._pkmu_gradient

    #---------------------------------------------------------------------------
    # main functions
    #---------------------------------------------------------------------------
    def set_free_parameters(self, theta):
        """
        Given an array of values `theta`, set the free parameters of
        attr:`fit_params`

        Notes
        -----
        If any free parameter values are outside their bounds, the
        model will not be updated and `False` will be returned

        Returns
        -------
        valid_model : bool
            return `True/False` flag indicating if we were able
            to successfully set the free parameters and update the model
        """
        return self.fit_params.set_free_parameters(theta)

    def get_grad_model_callable(self, data, transfers, stat_ids, 
                                 model_params=None, theory_decorator={}):
        """
        Get the callable to evaluate the gradient of the model.
        """
        # the flattened (k,mu) pairs for evaluating the model
        # NOTE: this allows us to evaluate the model only ONCE
        k, mu, slices = self.get_kmu_pairs(transfers)

        def evaluate(theta, pool=None, epsilon=1e-4, numerical=False):

            # update model parameters first?
            if model_params is not None:
                self.model.update(**model_params)

            # evaluate the P(k,mu) gradient
            gradient = self.pkmu_gradient(k, mu, theta, 
                                          pool=pool, 
                                          epsilon=epsilon, 
                                          numerical=numerical)

            # apply to transfer for gradient of each parameter
            grad_lnlike = []
            for i in range(self.ndim):
                grad_lnlike.append(apply_transfers(gradient[i], data, transfers, 
                                                    stat_ids, slices, theory_decorator))

            return np.asarray(grad_lnlike)

        return evaluate

    def get_model_callable(self, data, transfers, stat_ids, 
                            model_params=None,
                            theory_decorator={}):
        """
        Return the flattened theory prediction corresponding to the statistics
        in the ``data`` object.

        Parameters
        ----------
        data : PowerData
            the data class, which tells the theory which statistics to compute
            and what basis to evaluate the theory in, e.g., multipoles, wedges,
            window-convolved, etc
        transfers : list
            the list of the transfer functions to apply
        stat_ids : dict
            dictionary with keys of the relevant statistics and values are
            identitifers, e.g., ell or center mu values
        model_params : dict, optional
            a dictionary of model parameters to update
        theory_decorator : dict, optional
            dictionary of decorators to apply to the theory predictions for
            individual data statistics
        """
        # the flattened (k,mu) pairs for evaluating the model
        # NOTE: this allows us to evaluate the model only ONCE
        k, mu, slices = self.get_kmu_pairs(transfers)

        def evaluate():

            # update model parameters first?
            if model_params is not None:
                self.model.update(**model_params)

            # evaluate the P(k,mu) for the (k,mu) pairs we need
            P = self.model.power(k,mu)

            # apply the transfers to the power
            return apply_transfers(P, data, transfers, stat_ids, slices, theory_decorator)

        return evaluate

    def get_kmu_pairs(self, transfers):
        """
        Compute the flattened ``k`` and ``mu`` values needed to evaluate the
        theory prediction, given the transfer functions defined by ``data``.

        This also computes the slices needed to recover the pairs for
        individual transfer functions.
        """
        start = 0
        slices = []
        k = []; mu = []
        for i, t in enumerate(transfers):
            k.append(t.flatk)
            mu.append(t.flatmu)
            N = len(t.flatk)
            slices.append(slice(start, start+N))
            start += N

        return np.concatenate(k), np.concatenate(mu), slices

def apply_transfers(P, data, transfers, stat_ids, slices, theory_decorator):
    """
    Apply one (or more) transfer functions to the input P(k,mu) values.

    Parameters
    ----------
    P : xarray.DataArray
        the power values calculated on the (k,mu) grid
    data : PowerData
        the data object
    transfers : list
        the list of transfer objects to apply
    stat_ids : dict
        dictionary with keys of the relevant statistics and values are
        identifers, e.g., ell or center mu values
    slices : list
        the list of slices to slice the power result
    theory_decorator : dict
        decorator to run after the transfer function is applied
    """
    # determine which variables specify the second dimension of the basis
    # based on the mode, pkmu or poles
    dim = 'ell' if data.mode == 'poles' else 'mu'

    # apply the transfer function to the correct slice of P(k,mu)
    results = []
    for i, t in enumerate(transfers):
        results.append(t(P[slices[i]]))

    # concatenate results into a single array if we had multiple transfers
    if len(results) > 1:
        result = xr.concat(results, dim=results[0].dims[-1])
    else:
        result = results[0]

    # format the results
    toret = []
    for stat_name in stat_ids:

        # this is either ell or mu_cen for this statistic
        binval = stat_ids[stat_name]
        m = data.measurements[data.statistics.index(stat_name)]

        # make into a list if not
        # NOTE: this allows us to support multiple bin values per statistic
        if not isinstance(binval, list):
            binval = [binval]

        # compute the final theory prediction for this data statistic
        theory = []
        for bb in binval:

            # select the proper binval from the DataArray
            r = result.sel(**{dim:bb})

            # interpolate the window function results
            if isinstance(transfers[0], WindowFunctionTransfer):
                spl = spline(r['k'], r)
                theory.append(spl(m.k))
            # remove out of range values from Gridded Transfer results
            elif isinstance(transfers[0], gridded_transfers):
                theory.append(r.values[r.notnull()])
            # result already has the proper k binning
            else:
                theory.append(r.values)

        # apply any theory decorators for this statistic
        dec = theory_decorator.get(stat_name, None)
        if dec is not None:
            dec = getattr(decorators, dec)
            theory = dec(*theory)
        else:
            assert len(theory) == 1
            theory = theory[0]

        # final theory should be an array
        if not isinstance(theory, np.ndarray):
            msg = "error computing theory prediction for '%s'; " %stat
            msg += "maybe a theory decorator issue?"
            raise RuntimeError(msg)

        toret.append(theory)

    return np.concatenate(toret)
