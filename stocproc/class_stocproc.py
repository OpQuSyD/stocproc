import numpy as np
from scipy.interpolate import InterpolatedUnivariateSpline
from .class_stocproc_kle import StocProc

class ComplexInterpolatedUnivariateSpline(object):
    r"""
    Univariant spline interpolator from scpi.interpolate in a convenient fashion to
    interpolate real and imaginary parts of complex data 
    """
    def __init__(self, x, y, k=2):
        self.re_spline = InterpolatedUnivariateSpline(x, np.real(y))
        self.im_spline = InterpolatedUnivariateSpline(x, np.imag(y))
        
    def __call__(self, t):
        return self.re_spline(t) + 1j*self.im_spline(t)

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
    def __init__(self, t_max, num_grid_points, seed=None, verbose=1, k=3):
        r"""
            :param t_max: specify time axis as [0, t_max]
            :param num_grid_points: number of equidistant times on that axis
            :param seed: if not ``None`` set seed to ``seed``
            :param verbose: 0: no output, 1: informational output, 2: (eventually some) debug info
            :param k: polynomial degree used for spline interpolation  
        """
        self._verbose = verbose
        self.t_max = t_max
        self.num_grid_points = num_grid_points
        self.t = np.linspace(0, t_max, num_grid_points)
        self._z = None
        self._interpolator = None
        self._k = k
        if seed is not None:
            np.random.seed(seed)
        self._one_over_sqrt_2 = 1/np.sqrt(2)

    def __call__(self, t):
        r"""
        :param t: time to evaluate the stochastic process as, float of array of floats
        evaluates the stochastic process via spline interpolation between the discrete process 
        """
        if self._interpolator is None:
            if self._verbose > 1:
                print("setup interpolator ...")
            self._interpolator = ComplexInterpolatedUnivariateSpline(self.t, self._z, k=self._k)
            if self._verbose > 1:
                print("done!")
                
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
        self._interpolator = None
        if seed != None:
            if self._verbose > 0:
                print("use seed", seed)
            np.random.seed(seed)
        if y is None:
            #random complex normal samples
            if self._verbose > 1:
                print("generate samples ...")
            y = np.random.normal(scale=self._one_over_sqrt_2, size = 2*self.get_num_y()).view(np.complex)
            if self._verbose > 1:
                print("done")        

        self._z = self._calc_z(y)

class StocProc_KLE(_absStocProc):
    r"""
    class to simulate stochastic processes using KLE method
        - Solve fredholm equation on grid with ``ng_fredholm nodes`` (trapezoidal_weights).
          If case ``ng_fredholm`` is ``None`` set ``ng_fredholm = num_grid_points``. In general it should
          hold ``ng_fredholm < num_grid_points`` and ``num_grid_points = 10*ng_fredholm`` might be a good ratio. 
        - Calculate discrete stochastic process (using interpolation solution of fredholm equation) with num_grid_points nodes
        - invoke spline interpolator when calling 
    """
    def __init__(self, r_tau, t_max, ng_fredholm, ng_fac=4, seed=None, sig_min=1e-5, verbose=1, k=3):
        r"""
            :param r_tau: auto correlation function of the stochastic process
            :param t_max: specify time axis as [0, t_max]
            :param seed: if not ``None`` set seed to ``seed``
            :param sig_min: eigenvalue threshold (see KLE method to generate stochastic processes)
            :param verbose: 0: no output, 1: informational output, 2: (eventually some) debug info
            :param k: polynomial degree used for spline interpolation  
             
        """
        self.ng_fac = ng_fac
        if ng_fac == 1:
            self.kle_interp = False
        else:
            self.kle_interp = True

        self.stocproc = StocProc.new_instance_with_trapezoidal_weights(r_tau   = r_tau,
                                                                       t_max   = t_max,
                                                                       ng      = ng_fredholm, 
                                                                       sig_min = sig_min,
                                                                       seed    = seed,
                                                                       verbose = verbose)
        
        ng = ng_fac * (ng_fredholm - 1) + 1  
        
        super().__init__(t_max=t_max, num_grid_points=ng, seed=seed, verbose=verbose, k=k)
                
        # this is only needed access the underlaying stocproc KLE class
        # in a convenient fashion 
        self._r_tau = self.stocproc._r_tau
        self._s = self.stocproc._s
        self._A = self.stocproc._A
        self.num_y = self.stocproc._num_ev
        
        self.verbose = verbose

    def _calc_z(self, y):
        r"""
        uses the underlaying stocproc class to generate the process (see :py:class:`StocProc` for details) 
        """
        self.stocproc.new_process(y)
        
        if self.kle_interp:
            #return self.stocproc.x_t_array(np.linspace(0, self.t_max, self.num_grid_points))
            return self.stocproc.x_t_mem_save(delta_t_fac = self.ng_fac)
        else:
            return self.stocproc.x_for_initial_time_grid()
        
    def get_num_y(self):
        return self.num_y
        

class StocProc_FFT(_absStocProc):
    r"""
        Simulate Stochastic Process using FFT method 
    """
    def __init__(self, spectral_density, t_max, num_grid_points, seed=None, verbose=0, k=3):
        super().__init__(t_max           = t_max, 
                         num_grid_points = num_grid_points, 
                         seed            = seed, 
                         verbose         = verbose,
                         k               = k)
        
        self.n_dft           = num_grid_points * 2 - 1
        delta_t              = t_max / (num_grid_points-1)
        self.delta_omega     = 2 * np.pi / (delta_t * self.n_dft)
          
        #omega axis
        omega = self.delta_omega*np.arange(self.n_dft)
        #reshape for multiplication with matrix xi
        self.sqrt_spectral_density_over_pi_times_sqrt_delta_omega = np.sqrt(spectral_density(omega) / np.pi) * np.sqrt(self.delta_omega) 
        
        if self._verbose > 0:
            print("stoc proc fft, spectral density sampling information:")
            print("  t_max      :", (t_max))
            print("  ng         :", (num_grid_points))
            
            print("  omega_max  :", (self.delta_omega * self.n_dft))
            print("  delta_omega:", (self.delta_omega))
            
    def _calc_z(self, y):
        weighted_integrand = self.sqrt_spectral_density_over_pi_times_sqrt_delta_omega * y 
        #compute integral using fft routine
        if self._verbose > 1:
            print("calc process via fft ...")
        z = np.fft.fft(weighted_integrand)[0:self.num_grid_points]
        if self._verbose > 1:
            print("done")
        return z

    def get_num_y(self):
        return self.n_dft            