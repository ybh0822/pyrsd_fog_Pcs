from ... import numpy as np
from ..results import LBFGSResults
from .. import logging
from . import tools, objectives, lbfgs

import functools
import time

logger = logging.getLogger('rsdfit.lbfgs_fitter')


def InitializeFromPrior(params):
    """
    Initialize by drawing from the prior
    """
    while True:
        p0 = np.array([p.get_value_from_prior(size=1) for p in params])
        for i, value in enumerate(p0):
            params[i].value = value

        if all(p.within_bounds() for p in params):
            break

    return p0


def InitializeWithScatter(params, x, scatter):
    """
    Initialize by drawing from the prior
    """
    scale = scatter*x

    while True:
        p0 = x + np.random.normal(scale=scale)
        for i, value in enumerate(p0):
            params[i].value = value

        if all(p.within_bounds() for p in params):
            break

    return p0


def run(params, fit_params, pool=None, init_values=None):
    """
    Perform nonlinear fitting of a system using `scipy.optimize`.

    Any kwargs passed will not be used
    """
    exception = None
    init_from = params['init_from'].value

    # draw initial values randomly from prior
    if init_from == 'prior':
        init_values = InitializeFromPrior(fit_params.free)

    # add some scatter to initial values
    elif init_from in ['fiducial', 'result']:
        scatter = params.get('init_scatter', 0.)
        if scatter > 0:
            init_values = InitializeWithScatter(
                fit_params.free, init_values, scatter)

    if init_values is None:
        raise ValueError(
            "please specify how to initialize the maximum-likelihood solver")

    epsilon = params.get('lbfgs_epsilon', 1e-4)
    numerical = params.get('lbfgs_numerical', False)
    numerical_from_lnlike = params.get('lbfgs_numerical_from_lnlike', False)
    use_priors = params.get('lbfgs_use_priors', True)
    options = params.get('lbfgs_options', {})
    scaling = params.get('lbfgs_rescale', True)
    options['test_convergence'] = params.get('test_convergence', False)

    if 'max_iter' in options and not options['test_convergence']:
        maxiter = options['max_iter']
        if isinstance(init_values, LBFGSResults):
            maxiter -= init_values.iterations
        logger.info(
            "running LBFGS for %d iterations and then stopping" % maxiter)

    # sort epsilon is a dictionary of values
    if isinstance(epsilon, dict):
        epsilon = np.array([epsilon.get(k, 1e-4)
                            for k in fit_params.free_names])

    # log some info
    if use_priors:
        logger.info("running LBFGS minimizer with priors")
    else:
        logger.info("running LBFGS minimizer without priors")

    # setup the logging header
    names = "   ".join(["%9s" % name for name in fit_params.free_names])
    logging.info('{0:4s}   {1:s}   {2:9s}'.format('Iter', names, 'F(X)'))

    # determine the objective functions
    if use_priors:
        f = functools.partial(objectives.minus_lnprob, scaling=scaling)
    else:
        f = functools.partial(objectives.minus_lnlike, scaling=scaling)
    # the derivative
    grad_kws = {}
    grad_kws['epsilon'] = epsilon
    grad_kws['pool'] = pool
    grad_kws['use_priors'] = use_priors
    grad_kws['scaling'] = scaling
    grad_kws['numerical'] = numerical
    grad_kws['numerical_from_lnlike'] = numerical_from_lnlike
    fprime = functools.partial(objectives.grad_minus_lnlike, **grad_kws)

    if numerical_from_lnlike:
        logger.info("computing gradient using numerical derivative of lnlike()")
    elif numerical:
        logger.info("computing gradient using numerical derivative of P(k,mu)")

    #--------------------------------------------------------------------------
    # run the algorithm, catching any errors
    #--------------------------------------------------------------------------
    # initialize the minimizer
    restart_data = {}
    if isinstance(init_values, LBFGSResults):

        # save some restart data
        restart_data['iteration'] = init_values.data['iteration']
        restart_data['funcalls'] = init_values.data['funcalls']

        # start from the last iteration of result
        init_values = np.array([init_values[name]
                                for name in fit_params.free_names])

    # rescale the parameters?
    unscaler = None
    if scaling:
        init_values = fit_params.scale(init_values)
        unscaler = fit_params.inverse_scale

    # iniitalize the minimizer
    minimizer = lbfgs.LBFGS(f, fprime, init_values, unscaler=unscaler)

    # restart config
    if len(restart_data):
        minimizer.data.update(restart_data)
        niter = minimizer.data['iteration']
        logger.warning(
            "LBFGS: continuing from previous optimziation (starting at iteration {})".format(niter))

    try:
        start = time.time()
        result = minimizer.run_nlopt(**options)
    except KeyboardInterrupt as e:
        exception = e
    except Exception as e:
        import traceback
        logger.warning("exception occured:\n%s" % traceback.format_exc())
        exception = e

    stop = time.time()

    #--------------------------------------------------------------------------
    # handle the results
    #--------------------------------------------------------------------------
    logger.warning("...LBFGS optimization finished. Time elapsed: {}".format(
        tools.hms_string(stop-start)))
    d = minimizer.data

    if scaling:
        d['curr_state'].X = fit_params.inverse_scale(d['curr_state'].X)

    # extract the values to put them in the feedback
    if exception or d['status'] <= 0:
        reason = minimizer.convergence_status
        msg = "nonlinear fit with method L-BFGS-B failed: %s" % reason
        logger.error(msg)
    else:
        args = (d['iteration'], d['funcalls'])
        logger.info(
            "nonlinear fit with method L-BFGS-B succeeded after %d iterations and %d function evaluations" % args)
        logger.info("   convergence message: %s" % d['status'])

    results = None
    if exception is None or isinstance(exception, KeyboardInterrupt):
        results = LBFGSResults(d, fit_params)
    return results, exception
