import bisect
import numpy as np

import simulation.util.value_cache
import simulation.model.eval

import measurements.all.box.data
import measurements.all.pw.values
import measurements.all.pw.correlation
import measurements.all.pw_nearest.data
import measurements.all.pw_nearest.correlation
import measurements.land_sea_mask.data
import measurements.util.data

import util.math.matrix
import util.cache

import util.logging
logger = util.logging.logger


DEFAULT_BOXES_T_DIM = 12

class DataBase:

    def __init__(self, model_options=None, job_setup=None):
        from .constants import CACHE_DIRNAME, BOXES_F_FILENAME, BOXES_DF_FILENAME

        logger.debug('Initiating {} with model_options {} and job_setup {}.'.format(self, model_options, job_setup))

        ## init values
        self._f_boxes_cache_filename = BOXES_F_FILENAME
        self._df_boxes_cache_filename = BOXES_DF_FILENAME
        
        self.default_boxes_t_dim = DEFAULT_BOXES_T_DIM
        
        ## init model
        self.model = simulation.model.eval.Model(model_options=model_options, job_setup=job_setup)
        
        ## init caches
        self.hdd_cache = simulation.util.value_cache.Cache(model_options=model_options, cache_dirname=CACHE_DIRNAME, use_memory_cache=True)
        self.memory_cache = util.cache.MemoryCache()
        self.memory_cache_with_parameters = simulation.util.value_cache.MemoryCache()
        
        ## init job setup
        if job_setup is None:
            job_setup = {}
        try:
            job_setup['name']
        except KeyError:
            job_setup['name'] = str(self)


    def __str__(self):
        return self.__class__.__name__

    
    ## boxes cache filenames

    def f_boxes_cache_filename(self, time_dim):
        return self._f_boxes_cache_filename.format(time_dim=time_dim)

    def df_boxes_cache_filename(self, time_dim):
        return self._df_boxes_cache_filename.format(time_dim=time_dim, step_size=self.model.derivative_options['step_size'])

    

    ## model output
    
    def f_boxes_calculate(self, parameters, time_dim=12):
        logger.debug('Calculating new model f_boxes with time dim {} for {}.'.format(time_dim, self))
        f_boxes = self.model.f_boxes(parameters, time_dim_desired=time_dim)
        f_boxes = np.asanyarray(f_boxes)
        return f_boxes

    def f_boxes(self, parameters, time_dim=12, use_memmap=False):
        ## calculate
        calculation_time_dim = max([time_dim, self.default_boxes_t_dim])
        calculation_function = lambda p: self.f_boxes_calculate(p, time_dim=calculation_time_dim)
        data = self.hdd_cache.get_value(parameters, self.f_boxes_cache_filename(calculation_time_dim), calculation_function, derivative_used=False, save_also_txt=False, use_memmap=use_memmap)
        
        ## average if needed
        if time_dim < self.default_boxes_t_dim:
            data = util.math.interpolate.change_dim(data, 1, time_dim)
        
        ## return
        assert data.shape[1] == time_dim
        return data



    def df_boxes_calculate(self, parameters, time_dim=12):
        logger.debug('Calculating new model df_boxes with time dim {} for {}.'.format(time_dim, self))
        df_boxes = self.model.df_boxes(parameters, time_dim_desired=time_dim)
        df_boxes = np.asanyarray(df_boxes)
        for i in range(1, df_boxes.ndim-1):
            df_boxes = np.swapaxes(df_boxes, i, i+1)
        return df_boxes


    def df_boxes(self, parameters, time_dim=12, use_memmap=False, as_shared_array=False):
        ## calculate df
        calculation_time_dim = max([time_dim, self.default_boxes_t_dim])
        calculation_function = lambda p: self.df_boxes_calculate(p, time_dim=calculation_time_dim)
        filename = self.df_boxes_cache_filename(calculation_time_dim)
        df_boxes = self.hdd_cache.get_value(parameters, filename, calculation_function, derivative_used=True, save_also_txt=False, use_memmap=use_memmap, as_shared_array=as_shared_array)
        
        ## if cached df has to many parameters, remove unwanted partial derivatives
        if df_boxes.shape[-1] > len(parameters):
            logger.debug('Cached df has more partial derivatives ({}) than needed ({}). Truncating df.'.format(df_boxes.shape[-1], len(parameters)))
            slices = (slice(None),) * (df_boxes.ndim - 1) + (slice(len(parameters)),)
            df_boxes = df_boxes[slices]
        ## if cached df has to few parameters, recalculate
        elif df_boxes.shape[-1] < len(parameters):
            logger.debug('Cached df has to few partial derivatives ({}) than needed ({}). Recalculating df.'.format(df_boxes.shape[-1], len(parameters)))
            df_boxes = calculation_function(parameters)
            self.hdd_cache.save_value(parameters, filename, df_boxes, derivative_used=True, save_also_txt=False)
        
        ## average time if needed
        if time_dim < self.default_boxes_t_dim:
            df_boxes = util.math.interpolate.change_dim(df_boxes, 1, time_dim)
        
        ## return
        assert df_boxes.shape[1] == time_dim
        assert df_boxes.shape[-1] == len(parameters)
        return df_boxes


    def f_calculate(self, parameters):
        raise NotImplementedError("Please implement this method.")
    
    def f(self, parameters):
        values = self.memory_cache_with_parameters.get_value(parameters, 'F', self.f_calculate)
        assert values.ndim == 1 and len(values) == self.m
        return values

    def df_calculate(self, parameters):
        raise NotImplementedError("Please implement this method.")

    def df(self, parameters):
        ## get value form hdd cache        
        df = self.memory_cache_with_parameters.get_value(parameters, 'DF', self.df_calculate)
        assert df.ndim == 2 and len(df) == self.m and df.shape[1] in [len(parameters), len(parameters)+1]
        
        ## if cached df has to many parameters, remove unwanted partial derivatives
        if df.shape[-1] > len(parameters):
            logger.debug('Cached df has more partial derivatives ({}) than needed ({}). Truncating df.'.format(df.shape[-1], len(parameters)))
            slices = (slice(None),) * (df.ndim - 1) + (slice(len(parameters)),)
            df = df[slices]

        ## if cached df has to few parameters, recalculate
        elif df.shape[-1] < len(parameters):
            logger.debug('Cached df has to few partial derivatives ({}) than needed ({}). Recalculating df.'.format(df.shape[-1], len(parameters)))
            df = self.df_calculate(parameters)
            self.memory_cache_with_parameters.save_value(parameters, 'DF', df)
        
        ## return
        return df


    ## m
    
    @property
    def m(self):
        return self.memory_cache.get_value('m', lambda: len(self.results))

    ## results

    def results_calculate(self):
        raise NotImplementedError("Please implement this method")

    @property
    def results(self):
        values = self.memory_cache.get_value('results', lambda: self.results_calculate())
        assert values.ndim == 1
        return values


    ## deviation

    def deviations_calculate(self):
        raise NotImplementedError("Please implement this method")

    @property
    def deviations(self):
        values = self.memory_cache.get_value('deviations', lambda: self.deviations_calculate())
        assert values.ndim == 1 and len(values) == self.m
        return values

    @property
    def inverse_deviations(self):
        values = self.memory_cache.get_value('inverse_deviations', lambda: 1 / self.deviations)
        assert values.ndim == 1 and len(values) == self.m
        return values

    @property
    def variances(self):
        values = self.memory_cache.get_value('variances', lambda: self.deviations**2)
        assert values.ndim == 1 and len(values) == self.m
        return values

    @property
    def inverse_variances(self):
        values = self.memory_cache.get_value('inverse_variances', lambda: 1 / self.variances)
        assert values.ndim == 1 and len(values) == self.m
        return values

    @property
    def average_variance(self):
        values = self.memory_cache.get_value('average_variance', lambda: self.variances.mean())
        assert np.isfinite(values)
        return values

    @property
    def inverse_average_variance(self):
        values = self.memory_cache.get_value('inverse_average_variance', lambda: 1 / self.average_variance)
        assert np.isfinite(values)
        return values



    ## deviation boxes

    def deviations_boxes(self, time_dim=12, as_shared_array=False):
        def calculate():
            ## calculate
            calculation_time_dim = max([time_dim, self.default_boxes_t_dim])
            data = measurements.all.pw.values.deviation_TMM(t_dim=calculation_time_dim)
            
            ## average if needed
            if time_dim < self.default_boxes_t_dim:
                data = util.math.interpolate.change_dim(data, 1, time_dim)
        
            ## return
            assert data.shape[1] == time_dim
            return data
        
        return self.memory_cache.get_value('deviations_boxes_{}'.format(time_dim), calculate, as_shared_array=as_shared_array)

    def inverse_deviations_boxes(self, time_dim=12, as_shared_array=False):
        return self.memory_cache.get_value('inverse_deviations_boxes_{}'.format(time_dim), lambda: 1 / self.deviations_boxes(time_dim=time_dim), as_shared_array=as_shared_array)



class DataBaseHDD(DataBase):
    
    def __init__(self, *args, F_cache_filename=None, DF_cache_filename=None, **kargs):
        logger.debug('Initiating {} with F_cache_filename {} and DF_cache_filename {}.'.format(self, F_cache_filename, DF_cache_filename))
        super().__init__(*args, **kargs)
        self._F_cache_filename = F_cache_filename
        self._DF_cache_filename = DF_cache_filename

    @property
    def F_cache_filename(self):
        return self._F_cache_filename

    @property
    def DF_cache_filename(self):
        return self._DF_cache_filename.format(step_size=self.model.derivative_options['step_size'])
        
    
    def f(self, parameters):
        values = self.hdd_cache.get_value(parameters, self.F_cache_filename, self.f_calculate, derivative_used=False, save_also_txt=False)
        assert values.ndim == 1 and len(values) == self.m
        return values

    def df(self, parameters):
        ## get value form hdd cache
        df = self.hdd_cache.get_value(parameters, self.DF_cache_filename, self.df_calculate, derivative_used=True, save_also_txt=False)
        assert df.ndim == 2 and len(df) == self.m
        
        ## if cached df has to many parameters, remove unwanted partial derivatives
        if df.shape[-1] > len(parameters):
            logger.debug('Cached df has more partial derivatives ({}) than needed ({}). Truncating df.'.format(df.shape[-1], len(parameters)))
            slices = (slice(None),) * (df.ndim - 1) + (slice(len(parameters)),)
            df = df[slices]        
        ## if cached df has to few parameters, recalculate
        elif df.shape[-1] < len(parameters):
            logger.debug('Cached df has to few partial derivatives ({}) than needed ({}). Recalculating df.'.format(df.shape[-1], len(parameters)))
            df = self.df_calculate(parameters)
            self.hdd_cache.save_value(parameters, self.DF_cache_filename, df, derivative_used=True, save_also_txt=False)
        
        ## return
        assert df.shape[1] == len(parameters)
        return df
    



class WOA(DataBaseHDD):

    def __init__(self, model_options=None, job_setup=None):
        ## super constructor
        from .constants import WOA_F_FILENAME, WOA_DF_FILENAME
        super().__init__(model_options=model_options, job_setup=job_setup, F_cache_filename=WOA_F_FILENAME, DF_cache_filename=WOA_DF_FILENAME)

        ## compute annual box index
        from measurements.po4.woa.data13.constants import ANNUAL_THRESHOLD
        from simulation.model.constants import METOS_Z_LEFT
        self.ANNUAL_THRESHOLD_INDEX = bisect.bisect_right(METOS_Z_LEFT, ANNUAL_THRESHOLD)



    def _get_data_with_annual_averaged(self, data, annual_factor=1):
        data_monthly = data[:,:,:,:,:self.ANNUAL_THRESHOLD_INDEX][self.mask[:,:,:,:,:self.ANNUAL_THRESHOLD_INDEX]]
        data_annual = np.average(data[:,:,:,:,self.ANNUAL_THRESHOLD_INDEX:], axis=1)[self.mask[:,0,:,:,self.ANNUAL_THRESHOLD_INDEX:]] * annual_factor
        return np.concatenate([data_monthly, data_annual], axis=0)


    ## model output

    def f_calculate(self, parameters):
        f_boxes = self.f_boxes(parameters)
        F = self._get_data_with_annual_averaged(f_boxes)
        return F


    def df_calculate(self, parameters):
        df_boxes = self.df_boxes(parameters)
        DF = self._get_data_with_annual_averaged(df_boxes)
        return DF


    ## devitation

    @property
    def mean_deviations_boxes(self):
        return self.memory_cache.get_value('mean_deviations_boxes', lambda: (measurements.all.box.data.variances() / measurements.all.box.data.nobs())**(1/2))

    def deviations_calculate(self):
        return self._get_data_with_annual_averaged(self.mean_deviations_boxes, annual_factor=1/12)


    ## measurements

    @property
    def mask(self):
        return self.memory_cache.get_value('mask', lambda: measurements.all.box.data.nobs() > 0)

    @property
    def results_boxes(self):
        return self.memory_cache.get_value('results_boxes', lambda: measurements.all.box.data.means())

    def results_calculate(self):
        return self._get_data_with_annual_averaged(self.results_boxes)


    ## diff

    def diff_boxes(self, parameters, normalize_with_deviation=False, no_data_value=np.inf):
        results_boxes = self.results_boxes
        results_boxes[np.logical_not(self.mask)] = no_data_value
        diff = results_boxes - self.f_boxes(parameters)
        if normalize_with_deviation:
            diff = diff / self.mean_deviations_boxes
        return diff




class WOD_Base(DataBase):

    def __init__(self, *args, **kargs):
        super().__init__(*args, **kargs)


    ## measurements

    @property
    def points(self):
        values = self.memory_cache.get_value('points', lambda: self.points_calculate())
        assert len(values) == 2 
        assert all([ndim == 2 for ndim in map(lambda a: a.ndim, values)])
        return values


    @property
    def m_dop(self):
        return self.memory_cache.get_value('m_dop', lambda: len(self.points[0]))

    @property
    def m_po4(self):
        return self.memory_cache.get_value('m_po4', lambda: len(self.points[1]))

    @property
    def m(self):
        m = super().m
        assert self.m_dop + self.m_po4
        return m


    ## correlation matrix

    def correlation_matrix_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        raise NotImplementedError("Please implement this method.")

    def correlation_matrix(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        return self.memory_cache.get_value('correlation_matrix_{:0>2}_{:0>2}_{}'.format(min_measurements, max_year_diff, positive_definite_approximation_min_diag_value), lambda: self.correlation_matrix_calculate(min_measurements, max_year_diff=max_year_diff, positive_definite_approximation_min_diag_value=positive_definite_approximation_min_diag_value))


    def correlation_matrix_cholesky_decomposition_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        raise NotImplementedError("Please implement this method.")

    def correlation_matrix_cholesky_decomposition(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        return self.memory_cache.get_value('correlation_matrix_cholesky_decomposition_{:0>2}_{:0>2}_{}'.format(min_measurements, max_year_diff, positive_definite_approximation_min_diag_value), lambda: self.correlation_matrix_cholesky_decomposition_calculate(min_measurements, max_year_diff=max_year_diff, positive_definite_approximation_min_diag_value=positive_definite_approximation_min_diag_value))




class WOD(DataBaseHDD, WOD_Base):

    def __init__(self, model_options=None, job_setup=None):
        from .constants import WOD_F_FILENAME, WOD_DF_FILENAME
        super().__init__(model_options=model_options, job_setup=job_setup, F_cache_filename=WOD_F_FILENAME, DF_cache_filename=WOD_DF_FILENAME)


    ## model output

    def f_calculate(self, parameters):
        ## calculate f
        (f_dop, f_po4) = self.model.f_points(parameters, self.points)
        F = np.concatenate([f_dop, f_po4])
        return F


    def df_calculate(self, parameters):
        ## calculate df
        (df_dop, df_po4) = self.model.df_points(parameters, self.points)
        DF = np.concatenate([df_dop, df_po4], axis=-1)
        DF = np.swapaxes(DF, 0, 1)
        return DF



    ## deviation

    def deviations_calculate(self):
        (deviation_dop, deviation_po4) = measurements.all.pw.values.deviation()
        deviations = np.concatenate([deviation_dop, deviation_po4])
        return deviations


    ## measurements

    def points_calculate(self):
        return measurements.all.pw.values.points()


    def results_calculate(self):
        results = np.concatenate(measurements.all.pw.values.results())
        return results


    ## correlation matrix

    def correlation_matrix_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        return measurements.all.pw.correlation.CorrelationMatrix(min_measurements=min_measurements, max_year_diff=max_year_diff, positive_definite_approximation_min_diag_value=positive_definite_approximation_min_diag_value).correlation_matrix_positive_definite


    def correlation_matrix_cholesky_decomposition_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        return measurements.all.pw.correlation.CorrelationMatrix(min_measurements=min_measurements, max_year_diff=max_year_diff, positive_definite_approximation_min_diag_value=positive_definite_approximation_min_diag_value).correlation_matrix_cholesky_decomposition



    ## correlation methods for P3 correlation

    def correlation_parameters(self, parameters):
        from simulation.optimization.constants import COST_FUNCTION_CORRELATION_PARAMETER_FILENAME
        correlation_parameters = self.get_file(parameters, COST_FUNCTION_CORRELATION_PARAMETER_FILENAME)
        return correlation_parameters


    def check_regularity(self, correlation_parameters):
        [a, b, c] = correlation_parameters
        n = self.m_dop
        m = self.m_po4

        regularity_factor = (1+(n-1)*a) * (1+(m-1)*b) - n*m*c**2

        if regularity_factor <= 0:
            raise util.math.matrix.SingularMatrixError('The correlation matrix with correlation parameters (a, b, c) = {} is singular. It has to be (1+(n-1)*a) * (1+(m-1)*b) - n*m*c**2 > 0'.format(correlation_parameters))


    def ln_det_correlation_matrix(self, correlation_parameters):
        [a, b, c] = correlation_parameters
        n = self.m_dop
        m = self.m_po4

        self.check_regularity(correlation_parameters)
        ln_det = (n-1)*np.log(1-a) + (m-1)*np.log(1-b) + np.log((1+(n-1)*a) * (1+(m-1)*b) - n*m*c**2)

        return ln_det


    def project(self, values, split_index, projected_value_index=None):
        if projected_value_index in (0, 1):
            values = (values[:split_index], values[split_index:])

            if projected_value_index == 0:
                values_squared = []

                for i in range(len(values)):
                    value = values[i]
                    value_matrix = util.math.matrix.convert_to_matrix(value, dtype=np.float128)
                    value_squared = np.array(value_matrix.T * value_matrix)
                    value_squared = util.math.matrix.convert_matrix_to_array(value_squared, dtype=np.float128)
                    values_squared.append(value_squared)

                values_squared = np.array(values_squared)

                return values_squared

            elif projected_value_index == 1:
                values_summed = []

                for i in range(len(values)):
                    value = values[i]
                    value_summed = np.sum(value, axis=0, dtype=np.float128)

                    values_summed.append(value_summed)

                values_summed = np.array(values_summed)

                return values_summed

        elif projected_value_index is None:
            return (self.project(values, split_index, projected_value_index=0), self.project(values, split_index, projected_value_index=1))

        else:
            raise ValueError('Unknown projected_value_index: projected_value_index must be 0, 1 or None.')



    def projected_product_inverse_correlation_matrix_both_sides(self, factor_projected, correlation_parameters):
        ## unpack
        assert len(factor_projected) == 2
        (factor_squared, factor_summed) = factor_projected
        assert len(factor_squared) == 2
        assert len(factor_summed) == 2

        ## get correlation parameters
        [a, b, c] = correlation_parameters
        n = self.m_dop
        m = self.m_po4

        ## check regularity
        self.check_regularity(correlation_parameters)

        ## calculate first product part
        product = factor_squared[0] / (1-a) + factor_squared[1] / (1-b)

        ## calculate core matrix (2x2)
        A = np.matrix([[a, c], [c, b]])
        W = np.matrix([[1-a, 0], [0, 1-b]])
        D = np.matrix([[n, 0], [0, m]])
        H = W + D * A
        core = W.I * A * H.I * W.I

        ## calculate second product part
        factor_summed_matrix = util.math.matrix.convert_to_matrix(factor_summed)
        product += factor_summed_matrix.T * core * factor_summed_matrix

        ## return product
        product = np.array(product)
        if product.size == 1:
            product = product[0, 0]

        return product



    ## diff

    def diff(self, parameters, normalize_with_deviation=False):
        diff = self.results - self.F(parameters)
        if normalize_with_deviation:
            diff = diff / self.deviations
        return diff


    def convert_to_boxes(self, data, t_dim=12, no_data_value=np.inf):
        def convert_to_boxes_with_points(points, data):
            assert len(points) == len(data)

            lsm = measurements.land_sea_mask.data.LandSeaMaskTMM(t_dim=t_dim, t_centered=False)
            m = measurements.util.data.Measurements()
            m.append_values(points, data)
            m.transform_indices_to_lsm(lsm)
            data_map = lsm.insert_index_values_in_map(m.means(), no_data_value=no_data_value)

            return data_map

        data_dop_map = convert_to_boxes_with_points(self.points[0], data[:self.m_dop])
        data_po4_map = convert_to_boxes_with_points(self.points[1], data[self.m_dop:])
        data_map = [data_dop_map, data_po4_map]

        return data_map



class WOD_TMM(WOD_Base):
    
    def __init__(self, *args, max_land_boxes=0, **kargs):
        self.max_land_boxes = max_land_boxes
        logger.debug('Initiating {} with max_land_boxes {}.'.format(self, max_land_boxes))
        super().__init__(*args, **kargs)
        self.wod = WOD(*args, **kargs)
        self.lsm = measurements.land_sea_mask.data.LandSeaMaskTMM()
    

    def __str__(self):
        return '{}_{}'.format(self.__class__.__name__, self.max_land_boxes)


    def points_near_water_mask_calculate(self):
        return measurements.all.pw_nearest.data.points_near_water_mask(self.lsm, max_land_boxes=self.max_land_boxes)

    @property
    def points_near_water_mask(self):
        return self.memory_cache.get_value('points_near_water_mask', lambda: self.points_near_water_mask_calculate())

    @property
    def points_near_water_mask_concatenated(self):
        return np.concatenate(self.points_near_water_mask)
    

    def points_calculate(self):
        points_near_water_mask = self.points_near_water_mask
        points = self.wod.points
        points = list(points)
        for i in range(len(points)):
            points[i] = points[i][points_near_water_mask[i]]
        return points


    def results_calculate(self):
        return self.wod.results[self.points_near_water_mask_concatenated]


    def deviations_calculate(self):
        return self.wod.deviations[self.points_near_water_mask_concatenated]


    def f_calculate(self, parameters):
        return self.wod.f(parameters)[self.points_near_water_mask_concatenated]

    def df_calculate(self, parameters):
        return self.wod.df(parameters)[self.points_near_water_mask_concatenated]


    ## correlation matrix

    def correlation_matrix_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        return measurements.all.pw_nearest.correlation.CorrelationMatrix(min_measurements=min_measurements, max_year_diff=max_year_diff, positive_definite_approximation_min_diag_value=positive_definite_approximation_min_diag_value, lsm=self.lsm, max_land_boxes=self.max_land_boxes).correlation_matrix_positive_definite

    def correlation_matrix_cholesky_decomposition_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.1):
        return measurements.all.pw_nearest.correlation.CorrelationMatrix(min_measurements=min_measurements, max_year_diff=max_year_diff, positive_definite_approximation_min_diag_value=positive_definite_approximation_min_diag_value, lsm=self.lsm, max_land_boxes=self.max_land_boxes).correlation_matrix_cholesky_decomposition



class OLDWOD(WOD):

    def deviations_calculate(self):
        import os.path
        import measurements.dop.pw.deviation
        import measurements.land_sea_mask.data
        from measurements.constants import BASE_DIR
        sample_lsm = measurements.land_sea_mask.data.LandSeaMaskWOA13R(t_dim=52) 
        deviation_dop = measurements.dop.pw.deviation.total_deviation_for_points(sample_lsm=sample_lsm)
        file = os.path.join(BASE_DIR, 'po4/wod13/analysis/deviation/old/interpolated_deviation_lexsorted_points_0.1,2,0.2,1.npy')
        logger.debug('Loading OLD deviations: {}.'.format(file))
        deviation_po4 = np.load(file)
        deviations = np.concatenate([deviation_dop, deviation_po4])
        return deviations


    def correlation_matrix_cholesky_decomposition_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.01):
        if positive_definite_approximation_min_diag_value != 0.01:
            raise NotImplementedError("Not implemented!")
        import os.path
        import util.io.object
        from measurements.constants import BASE_DIR
        file = os.path.join(BASE_DIR, 'all/pw/correlation/old/lsm_48_woa13r/correlation_matrix.min_{}_measurements.max_inf_year_diff.positive_definite.default_ordering.reordering_True.min_diag_1e-02.cholesky_factors.csc.ppy'.format(min_measurements))
        logger.debug('Loading OLD correlation: {}.'.format(min_measurements))
        return util.io.object.load(file)
        
class OLDWOD_TMM(WOD_TMM):
    
    def __init__(self, *args, max_land_boxes=0, **kargs):
        logger.debug('Initiating OLD_WOD_TMM.'.format(self, max_land_boxes))
        super().__init__(*args, max_land_boxes=max_land_boxes, **kargs)
        self.wod = OLDWOD(*args, **kargs)

    def correlation_matrix_cholesky_decomposition_calculate(self, min_measurements, max_year_diff=float('inf'), positive_definite_approximation_min_diag_value=0.01):
        if positive_definite_approximation_min_diag_value != 0.01:
            raise NotImplementedError("Not implemented!")
        import os.path
        import util.io.object
        from measurements.constants import BASE_DIR
        file = os.path.join(BASE_DIR, 'all/pw_nearest_lsm_tmm_{}/correlation/old/lsm_48_woa13r/correlation_matrix.min_{}_measurements.max_inf_year_diff.positive_definite.default_ordering.reordering_True.min_diag_1e-02.cholesky_factors.csc.ppy'.format(self.max_land_boxes, min_measurements))
        logger.debug('Loading OLD correlation: {}.'.format(self.max_land_boxes, min_measurements))
        return util.io.object.load(file)
    


## init

def init_data_base(data_kind, model_options=None, job_setup=None):
    db_args = ()
    db_kargs = {'model_options': model_options, 'job_setup': job_setup}
    if data_kind.upper() == 'WOA':
        return WOA(*db_args, **db_kargs)
    elif data_kind.upper() == 'WOD':
        return WOD(*db_args, **db_kargs)
    elif data_kind.upper().startswith('WOD'):
        data_kind_splitted = data_kind.split('.')
        assert len(data_kind_splitted) == 2 and data_kind_splitted[0] == 'WOD'
        max_land_boxes = int(data_kind_splitted[1])
        return WOD_TMM(*db_args, max_land_boxes=max_land_boxes, **db_kargs)
    elif data_kind.upper().startswith('OLDWOD'):
        data_kind_splitted = data_kind.split('.')
        assert len(data_kind_splitted) == 2 and data_kind_splitted[0] == 'OLDWOD'
        max_land_boxes = int(data_kind_splitted[1])
        return OLDWOD_TMM(*db_args, max_land_boxes=max_land_boxes, **db_kargs)
    else:
        raise ValueError('Data_kind {} unknown. Must be "WOA", "WOD" or "WOD.".'.format(data_kind))



## Family

class Family():
    
    member_classes = {}
    
    def __init__(self, **cf_kargs):
        ## chose member classes
        data_kind = cf_kargs['data_kind'].upper()
        try:
            member_classes_list = self.member_classes[data_kind]
        except KeyError:
            raise ValueError('Data_kind {} unknown. Must be in {}.'.format(data_kind, list(self.member_classes.keys())))

        ## init members
        family = []
        for member_class, additional_arguments in member_classes_list:
            for additional_kargs in additional_arguments:
                cf_kargs_member_class = cf_kargs.copy()
                cf_kargs_member_class.update(additional_kargs)
                member = member_class(**cf_kargs_member_class)
                family.append(member)
        
        ## set same database
        for i in range(1, len(family)):
            family[i].data_base = family[0].data_base

        ## set family
        logger.debug('Cost function family for data kind {} with members {} initiated.'.format(data_kind, list(map(lambda x: str(x), family))))
        self.family = family


    def get_function_value(self, function):
        assert callable(function)

        value = function(self.family[0])
        for member in self.family:
            function(member)

        return value