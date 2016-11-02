"""
Stochastic Process Generators
=============================

Karhunen-Loève expansion
------------------------

This method samples stochastic processes using Karhunen-Loève expansion and
is implemented in the class :doc:`StocProc_KLE_tol </StocProc_KLE>`.

Setting up the class involves solving an eigenvalue problem which grows with
the time interval the process is simulated on. Further generating a new process
involves a multiplication with that matrix, therefore it scales quadratically with the
time interval. Nonetheless it turns out that this method requires less random numbers
than the Fast-Fourier method.


Fast-Fourier method
-------------------
simulate stochastic processes using Fast-Fourier method method :py:func:`stocproc.StocProc_FFT_tol`

Setting up this class is quite efficient as it only calculates values of the
associated spectral density. The number scales linear with the time interval of interest. However to achieve
sufficient accuracy many of these values are required. As the generation of a new process is based on
a Fast-Fouried-Transform over these values, this part is comparably lengthy.
"""
import numpy as np
import time

from . import method_kle
from . import method_fft
from . import stocproc_c
from .tools import ComplexInterpolatedUnivariateSpline

import logging
log = logging.getLogger(__name__)

class _absStocProc(object):
    r"""
    Abstract base class to stochastic process interface
    
    general work flow:
        - Specify the time axis of interest [0, t_max] and it resolution (number of grid points), :math:`t_i = i \frac{t_max}{N_t-1}.  
        - To evaluate the stochastic process at these points, a mapping from :math:`N_z` normal distributed 
          random complex numbers with :math:`\langle y_i y_j^\ast \rangle = 2 \delta_{ij}`
          to the stochastic process :math:`z_{t_i}` is needed and depends on the implemented method (:py:func:`_calc_z').
        - A new process should be generated by calling :py:func:`new_process'.
        - When the __call__ method is invoked the results will be interpolated between the :math:`z_t_i`.
        
      
    """
    def __init__(self, t_max, num_grid_points, seed=None, k=3):
        r"""
            :param t_max: specify time axis as [0, t_max]
            :param num_grid_points: number of equidistant times on that axis
            :param seed: if not ``None`` set seed to ``seed``
            :param verbose: 0: no output, 1: informational output, 2: (eventually some) debug info
            :param k: polynomial degree used for spline interpolation  
        """
        self.t_max = t_max
        self.num_grid_points = num_grid_points
        self.t = np.linspace(0, t_max, num_grid_points)
        self._z = None
        self._interpolator = None
        self._k = k
        self._seed = seed
        if seed is not None:
            np.random.seed(seed)
        self._one_over_sqrt_2 = 1/np.sqrt(2)
        self._proc_cnt = 0
        log.debug("init StocProc with t_max {} and {} grid points".format(t_max, num_grid_points))

    def __call__(self, t=None):
        r"""
        :param t: time to evaluate the stochastic process as, float of array of floats
        evaluates the stochastic process via spline interpolation between the discrete process 
        """
        if self._z is None:
            raise RuntimeError("StocProc_FFT has NO random data, call 'new_process' to generate a new random process")

        if t is None:
            return self._z
        else:
            if self._interpolator is None:
                t0 = time.time()
                self._interpolator = ComplexInterpolatedUnivariateSpline(self.t, self._z, k=self._k)
                log.debug("created interpolator [{:.2e}s]".format(time.time() - t0))
            return self._interpolator(t)
    
    def _calc_z(self, y):
        r"""
        maps the normal distributed complex valued random variables y to the stochastic process
        
        :return: the stochastic process, array of complex numbers 
        """
        pass
    
    def get_num_y(self):
        r"""
        :return: number of complex random variables needed to calculate the stochastic process 
        """
        pass        
    
    def get_time(self):
        r"""
        :return: time axis
        """
        return self.t
    
    def get_z(self):
        r"""
        use :py:func:`new_process` to generate a new process
        :return: the current process 
        """
        return self._z
    
    def new_process(self, y=None, seed=None):
        r"""
        generate a new process by evaluating :py:func:`_calc_z'
        
        When ``y`` is given use these random numbers as input for :py:func:`_calc_z`
        otherwise generate a new set of random numbers.
        
        :param y: independent normal distributed complex valued random variables with :math:`\sig_{ij}^2 = \langle y_i y_j^\ast \rangle = 2 \delta_{ij}
        :param seed: if not ``None`` set seed to ``seed`` before generating samples 
        """
        t0 = time.time()
        self._interpolator = None
        self._proc_cnt += 1
        if seed != None:
            log.info("use fixed seed ({})for new process".format(seed))
            np.random.seed(seed)
        if y is None:
            #random complex normal samples
            y = np.random.normal(scale=self._one_over_sqrt_2, size = 2*self.get_num_y()).view(np.complex)
        self._z = self._calc_z(y)
        log.debug("proc_cnt:{} new process generated [{:.2e}s]".format(self._proc_cnt, time.time() - t0))

METHOD = 'midp'

class StocProc_KLE(_absStocProc):
    r"""
    class to simulate stochastic processes using KLE method
        - Solve fredholm equation on grid with ``ng_fredholm nodes`` (trapezoidal_weights).
          If case ``ng_fredholm`` is ``None`` set ``ng_fredholm = num_grid_points``. In general it should
          hold ``ng_fredholm < num_grid_points`` and ``num_grid_points = 10*ng_fredholm`` might be a good ratio. 
        - Calculate discrete stochastic process (using interpolation solution of fredholm equation) with num_grid_points nodes
        - invoke spline interpolator when calling 
    """
    def __init__(self, r_tau, t_max, ng_fredholm, ng_fac=4, seed=None, sig_min=1e-5, k=3, align_eig_vec=False):
        r"""
            :param r_tau: auto correlation function of the stochastic process
            :param t_max: specify time axis as [0, t_max]
            :param seed: if not ``None`` set seed to ``seed``
            :param sig_min: eigenvalue threshold (see KLE method to generate stochastic processes)
            :param verbose: 0: no output, 1: informational output, 2: (eventually some) debug info
            :param k: polynomial degree used for spline interpolation             
        """

        # this lengthy part will be skipped when init class from dump, as _A and alpha_k will be stored
        t0 = time.time()
        if METHOD == 'midp':
            t, w = method_kle.get_mid_point_weights_times(t_max, ng_fredholm)
        elif METHOD == 'simp':
            t, w = method_kle.get_simpson_weights_times(t_max, ng_fredholm)

        r = self._calc_corr_matrix(t, r_tau)
        _eig_val, _eig_vec = method_kle.solve_hom_fredholm(r, w, sig_min ** 2)
        if align_eig_vec:
            for i in range(_eig_vec.shape[1]):
                s = np.sum(_eig_vec[:, i])
                phase = np.exp(1j * np.arctan2(np.real(s), np.imag(s)))
                _eig_vec[:, i] /= phase
        _sqrt_eig_val = np.sqrt(_eig_val)
        _A = w.reshape(-1, 1) * _eig_vec / _sqrt_eig_val.reshape(1, -1)
        ng_fine = ng_fac * (ng_fredholm - 1) + 1
        alpha_k = self._calc_corr_min_t_plus_t(s=np.linspace(0, t_max, ng_fine), bcf=r_tau)
        log.debug("new KLE StocProc class prepared [{:.2e}]".format(time.time() - t0))

        data = (_A, alpha_k, seed, k, t_max, ng_fac)
        self.__setstate__(data)

        # needed for getkey / getstate
        self.key = (r_tau, t_max, ng_fredholm, ng_fac, sig_min, align_eig_vec)

        # save these guys as they are needed to estimate the autocorrelation
        self._s = t
        self._w = w
        self._eig_val = _eig_val
        self._eig_vec = _eig_vec

    def _getkey(self):
        return self.__class__.__name__, self.key

    def __getstate__(self):
        return self._A, self.alpha_k, self._seed, self._k, self.t_max, self.ng_fac

    def __setstate__(self, state):
        self._A, self.alpha_k, seed, k, t_max, self.ng_fac = state
        if self.ng_fac == 1:
            self.kle_interp = False
        else:
            self.kle_interp = True
        self._one_over_sqrt_2 = 1 / np.sqrt(2)
        num_gp, self.num_y = self._A.shape
        ng_fine = self.ng_fac * (num_gp - 1) + 1
        super().__init__(t_max=t_max, num_grid_points=ng_fine, seed=seed, k=k)




    
    @staticmethod
    def _calc_corr_min_t_plus_t(s, bcf):
        bcf_n_plus = bcf(s-s[0])
        #    [bcf(-3)    , bcf(-2)    , bcf(-1)    , bcf(0), bcf(1), bcf(2), bcf(3)]
        # == [bcf(3)^\ast, bcf(2)^\ast, bcf(1)^\ast, bcf(0), bcf(1), bcf(2), bcf(3)]        
        return np.hstack((np.conj(bcf_n_plus[-1:0:-1]), bcf_n_plus))        
    
    @staticmethod
    def _calc_corr_matrix(s, bcf):
        """calculates the matrix alpha_ij = bcf(t_i-s_j)
        
        calls bcf only for s-s_0 and reconstructs the rest
        """
        n_ = len(s)
        bcf_n = StocProc_KLE._calc_corr_min_t_plus_t(s, bcf)
        # we want
        # r = bcf(0) bcf(-1), bcf(-2)
        #     bcf(1) bcf( 0), bcf(-1)
        #     bcf(2) bcf( 1), bcf( 0)
        r = np.empty(shape=(n_,n_), dtype = np.complex128)
        for i in range(n_):
            idx = n_-1-i
            r[:,i] = bcf_n[idx:idx+n_]
        return r
        
    def __calc_missing(self):
        raise NotImplementedError

    def _calc_z(self, y):
        if self.kle_interp:
            _a_tmp = np.tensordot(y, self._A, axes=([0], [1]))
            _num_gp = self._A.shape[0]
            return stocproc_c.z_t(delta_t_fac = self.ng_fac,
                                  N1          = _num_gp,
                                  alpha_k     = self.alpha_k,
                                  a_tmp       = _a_tmp,
                                  kahanSum    = True)

        else:
            return np.tensordot(y*self._sqrt_eig_val, self._eig_vec, axes=([0], [1])).flatten()
        
    def get_num_y(self):
        return self.num_y
    
    
    
class StocProc_KLE_tol(StocProc_KLE):
    r"""
        A class to simulate stochastic processes using Karhunen-Loève expansion (KLE) method.
        The idea is that any stochastic process can be expressed in terms of the KLE

        .. math:: Z(t) = \sum_i \sqrt{\lambda_i} Y_i u_i(t)

        where :math:`Y_i` and independent complex valued Gaussian random variables with variance one
        (:math:`\langle Y_i Y_j \rangle = \delta_{ij}`) and :math:`\lambda_i`, :math:`u_i(t)` are
        eigenvalues / eigenfunctions of the following Fredholm equation

        .. math:: \int_0^{t_\mathrm{max}} \mathrm{d}s R(t-s) u_i(s) = \lambda_i u_i(t)

        for a given positive integral kernel :math:`R(\tau)`. It turns out that the auto correlation
        :math:`\langle Z(t)Z^\ast(s) \rangle = R(t-s)` is given by that kernel.

        For the numeric implementation the integral equation has to be discretized


        - Solve fredholm equation on grid with ``ng_fredholm nodes`` (trapezoidal_weights).
          If case ``ng_fredholm`` is ``None`` set ``ng_fredholm = num_grid_points``. In general it should
          hold ``ng_fredholm < num_grid_points`` and ``num_grid_points = 10*ng_fredholm`` might be a good ratio.
        - Calculate discrete stochastic process (using interpolation solution of fredholm equation) with num_grid_points nodes
        - invoke spline interpolator when calling

        same as StocProc_KLE except that ng_fredholm is determined from given tolerance

        bla bla

    """
    
    def __init__(self, r_tau, t_max, tol=1e-2, ng_fac=4, seed=None, k=3, align_eig_vec=False):
        """this is init

        :param r_tau:
        :param t_max:
        :param tol:
        :param ng_fac:
        :param seed:
        :param k:
        :param align_eig_vec:
        """
        self.tol = tol
        kwargs = {'r_tau': r_tau, 't_max': t_max, 'ng_fac': ng_fac, 'seed': seed,
                  'sig_min': tol**2, 'k': k, 'align_eig_vec': align_eig_vec}
        self._auto_grid_points(**kwargs)
        # overwrite ng_fac in key from StocProc_KLE with value of tol
        # self.key = (r_tau, t_max, ng_fredholm, ng_fac, sig_min, align_eig_vec)
        self.key = (self.key[0], self.key[1], tol, self.key[3],self.key[4], self.key[5])

    def _init_StocProc_KLE_and_get_error(self, ng, **kwargs):
        super().__init__(ng_fredholm=ng, **kwargs)
            
        ng_fine = self.ng_fac*(ng-1)+1
        u_i_all_t =  stocproc_c.eig_func_all_interp(delta_t_fac = self.ng_fac,
                                                    time_axis   = self._s,
                                                    alpha_k     = self.alpha_k, 
                                                    weights     = self._w,
                                                    eigen_val   = self._eig_val,
                                                    eigen_vec   = self._eig_vec)    

        u_i_all_ast_s = np.conj(u_i_all_t)                  #(N_gp, N_ev)
        num_ev = len(self._eig_val)       
        tmp = self._eig_val.reshape(1, num_ev) * u_i_all_t  #(N_gp, N_ev)  
        recs_bcf = np.tensordot(tmp, u_i_all_ast_s, axes=([1],[1]))
        
        refc_bcf = np.empty(shape=(ng_fine,ng_fine), dtype = np.complex128)
        for i in range(ng_fine):
            idx = ng_fine-1-i
            refc_bcf[:,i] = self.alpha_k[idx:idx+ng_fine]
        
        err = np.max(np.abs(recs_bcf-refc_bcf)/np.abs(refc_bcf))
        return err
        
    
    def _auto_grid_points(self, **kwargs): 
        err = np.inf
        c = 2
        #exponential increase to get below error threshold
        while err > self.tol:
            c *= 2
            ng = 2*c + 1
            err = self._init_StocProc_KLE_and_get_error(ng, **kwargs)
            log.info("ng {} -> err {:.3e}".format(ng, err))
                       
        c_low = c // 2
        c_high = c
         
        while (c_high - c_low) > 1:            
            c = (c_low + c_high) // 2
            ng = 2*c + 1
            err = self._init_StocProc_KLE_and_get_error(ng, **kwargs)
            log.info("ng {} -> err {:.3e}".format(ng, err))
            if err > self.tol:
                c_low = c
            else:
                c_high = c


class StocProc_FFT_tol(_absStocProc):
    r"""
        Simulate Stochastic Process using FFT method 
    """
    def __init__(self, spectral_density, t_max, bcf_ref, intgr_tol=1e-2, intpl_tol=1e-2,
                 seed=None, k=3, negative_frequencies=False):
        if not negative_frequencies: 
            log.info("non neg freq only")
            # assume the spectral_density is 0 for w<0 
            # and decays fast for large w
            b = method_fft.find_integral_boundary(integrand = spectral_density, 
                                                  tol       = intgr_tol**2,
                                                  ref_val   = 1, 
                                                  max_val   = 1e6, 
                                                  x0        = 1)
            log.info("upper int bound b {:.3e}".format(b))
            a, b, N, dx, dt = method_fft.calc_ab_N_dx_dt(integrand = spectral_density,
                                                         intgr_tol = intgr_tol,
                                                         intpl_tol = intpl_tol,
                                                         t_max     = t_max,
                                                         a         = 0,
                                                         b         = b,
                                                         ft_ref    = lambda tau:bcf_ref(tau)*np.pi,
                                                         opt_b_only= True,
                                                         N_max     = 2**24)
            log.info("required tol results in N {}".format(N))
        else:
            log.info("use neg freq")
            # assume the spectral_density is non zero also for w<0 
            # but decays fast for large |w|
            b = method_fft.find_integral_boundary(integrand = spectral_density, 
                                                  tol       = intgr_tol**2,
                                                  ref_val   = 1, 
                                                  max_val   = 1e6, 
                                                  x0        = 1)
            a = method_fft.find_integral_boundary(integrand = spectral_density, 
                                                  tol       = intgr_tol**2,
                                                  ref_val   = -1, 
                                                  max_val   = 1e6, 
                                                  x0        = -1)            
            a, b, N, dx, dt = method_fft.calc_ab_N_dx_dt(integrand = spectral_density,
                                                              intgr_tol = intgr_tol, 
                                                              intpl_tol = intpl_tol, 
                                                              t_max     = t_max,
                                                              a         = a,
                                                              b         = b,
                                                              ft_ref    = lambda tau:bcf_ref(tau)*np.pi,
                                                              opt_b_only= False,
                                                              N_max     = 2**24)
            log.info("required tol result in N {}".format(N))

        assert abs(2*np.pi - N*dx*dt) < 1e-12
        num_grid_points = int(np.ceil(t_max/dt))+1
        t_max = (num_grid_points-1)*dt
        
        super().__init__(t_max           = t_max, 
                         num_grid_points = num_grid_points, 
                         seed            = seed,
                         k               = k)
        
        omega = dx*np.arange(N)
        self.yl = spectral_density(omega + a + dx/2) * dx / np.pi
        self.yl = np.sqrt(self.yl)
        self.omega_min_correction = np.exp(-1j*(a+dx/2)*self.t)   #self.t is from the parent class

    def __getstate__(self):
        return self.yl, self.num_grid_points, self.omega_min_correction, self.t_max, self._seed, self._k

    def __setstate__(self, state):
        self.yl, num_grid_points, self.omega_min_correction, t_max, seed, k = state
        super().__init__(t_max           = t_max,
                         num_grid_points = num_grid_points,
                         seed            = seed,
                         k               = k)
            
            
    def _calc_z(self, y): 
        z = np.fft.fft(self.yl * y)[0:self.num_grid_points] * self.omega_min_correction
        return z

    def get_num_y(self):
        return len(self.yl)