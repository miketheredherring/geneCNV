import logging
import numpy as np
from PloidyModel import PloidyModel


class ConvergenceAnalysis(object):
    """A class for analyzying convergence and metastability error of MCMC sampler, given specific data and parameters. """
    def __init__(self, cnv_support, hln_parameters, data, first_baseline_i, exclude_covar, n_iterations, burn_in_prop):
        self.cnv_support = cnv_support
        self.hln_parameters = hln_parameters
        self.data = data
        self.first_baseline_i = first_baseline_i
        self.exclude_covar = exclude_covar
        self.n_iterations = n_iterations
        self.burn_in_prop = burn_in_prop
        self.ploidy_model = None

    def gelman_rubin_analysis(self, num_chains, n_test_targets, iter_step_size=5000, max_iterations=25000, max_prop=0.5):
        """Run convergence analysis on MCMC sampler, given subject data and starting parameters. Uses Gelman-Rubin potential scale
        reduction factor (PSRF) on both intensities and overall log-likelihood to assess potential convergence across
        specified number of chains.
        Iterates through different chain-lengths and burn-in proportions to find parameters that satisfy convergence criteria.
        See Bayesian Data Analysis (Gelman, Third Edition) for full derivation.

        Returns the last chain (PloidyModel instance) which satisfied convergence criteria, burn-in proportion and n_iterations used.

        num_chains -- Number of chains to use in analysis, should be at least 2
        n_test_targets -- Total number of targets to be used in model (including baselines)
        iter_step_size -- Step size for increasing n_iterations
        max_iterations -- Maximum chain-length to attempt during optimization
        max_prop -- Maximum burn-in proportion to attempt during optimization
        """
        orig_burn_in_prop = self.burn_in_prop
        tries = 0

        psrf_loglikes = 2
        psrf_intensities = 2

        stored_data = [[None] * 2 for c_i in range(num_chains)]
        sampling_args = [[self.n_iterations] for c_i in range(num_chains)]

        # cutoff of 1.1 generally used in literature, a bit more slack for intensities
        while psrf_loglikes > 1.1 or np.mean(psrf_intensities) > 1.15:
            # increment burn-in proportion before chain length
            if self.burn_in_prop > max_prop or tries == 0:
                if tries > 0:
                    self.n_iterations += iter_step_size
                    # store previous values to allow for continuation of chains
                    sampling_args = [[iter_step_size, np.copy(posterior_ploidies[c_i]).T, np.copy(posterior_intensities[c_i]),  # pylint:disable=used-before-assignment
                                      np.copy(posterior_loglikes[c_i])] for c_i in range(num_chains)]    # pylint:disable=used-before-assignment

                posterior_loglikes = np.zeros((num_chains, self.n_iterations))
                posterior_ploidies = np.zeros((num_chains, self.n_iterations, n_test_targets))
                posterior_intensities = np.zeros((num_chains, self.n_iterations, n_test_targets))

                if self.n_iterations > max_iterations:
                    logging.warning(('Poor convergence even after {} iterations; '
                                     'checking for metastability error next. \nPSRF (log-likelihood): '
                                     '{}\nmean PSRF (intensities): {}'.format(max_iterations, psrf_loglikes, np.mean(psrf_intensities))))
                    self.n_iterations = max_iterations
                    self.burn_in_prop = orig_burn_in_prop + 0.05
                    break
                # reset burn-in proportion
                self.burn_in_prop = orig_burn_in_prop
                logging.info(('Performing Gelman-Rubin analysis with {} iterations and burn-in '
                              'prop of {}.'.format(self.n_iterations, self.burn_in_prop, psrf_loglikes, np.mean(psrf_intensities))))
                for c_i in range(num_chains):
                    self.ploidy_model = PloidyModel(self.cnv_support, self.hln_parameters, data=self.data, ploidy=stored_data[c_i][0],
                                                    intensities=stored_data[c_i][1], first_baseline_i=self.first_baseline_i, exclude_covar=self.exclude_covar)
                    self.ploidy_model.RunMCMC(*sampling_args[c_i])
                    posterior_loglikes[c_i] = self.ploidy_model.likelihoods
                    posterior_ploidies[c_i] = self.ploidy_model.mcmc_copy_data.T
                    posterior_intensities[c_i] = self.ploidy_model.mcmc_intens

                    stored_data[c_i][0] = np.copy(self.ploidy_model.ploidy)
                    stored_data[c_i][1] = np.copy(self.ploidy_model.intensities)


            # split each chain into two halves (after removing burn-in)
            # int round needed because of some nasty and unexpected floating point error
            split_loglikes = np.concatenate(np.split(posterior_loglikes[:, int(round(self.burn_in_prop * self.n_iterations)):], 2, axis=1))
            split_intensities = np.concatenate(np.split(posterior_intensities[:, int(round(self.burn_in_prop * self.n_iterations)):, :], 2, axis=1))
            chain_length = split_loglikes.shape[1]    # n_iterations * (1 - burn_in_prop) * 0.5
            chain_m = split_loglikes.shape[0]         # 2 * num_chains

            # compute PSRF for log likelihoods
            seq_mean_loglikes = np.mean(split_loglikes, axis=1)
            chain_mean_loglikes = np.mean(seq_mean_loglikes, axis=0)
            B_loglikes = (chain_length / float(chain_m - 1)) * np.sum(np.square(seq_mean_loglikes - chain_mean_loglikes), axis=0)

            in_seq_var_loglikes = (1. / (chain_length - 1)) * np.sum(np.square(split_loglikes - seq_mean_loglikes.reshape(-1, 1)), axis=1)
            W_loglikes = np.mean(in_seq_var_loglikes, axis=0)
            var_plus_loglikes = ((chain_length - 1.) / chain_length) * W_loglikes + (1. / chain_length) * B_loglikes
            psrf_loglikes = np.sqrt(var_plus_loglikes / W_loglikes)

            # compute PSRF for all intensities
            seq_mean_intensities = np.mean(split_intensities, axis=1)
            chain_mean_intensities = np.mean(seq_mean_intensities, axis=0)
            B_intensities = (chain_length / float(chain_m - 1)) * np.sum(np.square(seq_mean_intensities - chain_mean_intensities), axis=0)

            # this could possibly done more efficiently with broadcasting trick
            # subtract seq_means from each individual value at every iteration
            elem_diff = np.zeros(split_intensities.shape)
            for i in range(num_chains):
                elem_diff[i] = split_intensities[i] - seq_mean_intensities[i]

            in_seq_var_intensities = (1. / (chain_length - 1)) * np.sum(np.square(elem_diff), axis=1)
            W_intensities = np.mean(in_seq_var_intensities, axis=0)
            var_plus_intensities = ((chain_length - 1.) / chain_length) * W_intensities + (1. / chain_length) * B_intensities
            # note that last element is always 0
            psrf_intensities = np.sqrt(np.divide(var_plus_intensities[:-1], W_intensities[:-1]))

            logging.info('Burn-in prop: {}, PSRF (log-likelihood): {}, PSRF (intensities): {}'.format(self.burn_in_prop, psrf_loglikes, np.mean(psrf_intensities)))
            self.burn_in_prop += 0.05
            tries += 1

        self.burn_in_prop -= 0.05
        logging.info(('Completed Gelman-Rubin convergence analysis. Used {} iterations and {} burn-in prop.\nPSRF (log-likelihood): '
                      '{}\nmean PSRF (intensities): {}'.format(self.n_iterations, (self.burn_in_prop), psrf_loglikes, np.mean(psrf_intensities))))

    def metastability_error_analysis(self, norm_copy_num, grad_threshold=0.35, thresh_loglike_diff=-30, autocor_slice=50, max_tries=5):
        """Checks for metastability error (causing false positives) in MCMC sampling results. Compares optimized
        log-likelihood of normal ploidy state and reported ploidy state -- assumes that normal ploidy state should not
        have significantly lower optimized log-likelihood.
        If evidence for metastability error is found, will alternately try to find more appropriate burn-in
        (after mode switch) and rerun sampling with more iterations.

        Returns copy_posteriors calculated with acceptable burn-in and log-likelihood difference.
        Returns error if cannot find convergence conditions.

        norm_copy_num -- Normal ploidy state (used to generate normal copy numbers)
        grad_threshold -- Threshold for gradient of log-likelihood above which a mode switch is registered
        thresh_loglike_diff -- Lower bound for log-likelihood difference
        autocor_slice -- Autocor_slice to use in computing copy_posteriors
        max_tries -- Maximum number of attempts in finding convergence conditions
        """
        if self.ploidy_model is None:
            self.ploidy_model = PloidyModel(self.cnv_support, self.hln_parameters, data=self.data,
                                            first_baseline_i=self.first_baseline_i, exclude_covar=self.exclude_covar)
            self.ploidy_model.RunMCMC(self.n_iterations)

        copy_posteriors = self.ploidy_model.ReportMCMCData(int(round(self.burn_in_prop * self.n_iterations)), autocor_slice)
        loglike_diff = self.ploidy_model.LikelihoodComparison(norm_copy_num)

        tries = 0
        iter_step_size = int(round(self.n_iterations * 0.5))
        while loglike_diff < thresh_loglike_diff:
            logging.info('Run {}; trying {} iterations; latest loglike_diff: {}'.format(tries, self.n_iterations, loglike_diff))
            if tries > max_tries:
                logging.error('Metastability error: unable to reach convergence at most likely mode')
            # check if metastability error in this chain after increasing n_iterations
            if tries > 0:
                self.ploidy_model = PloidyModel(self.cnv_support, self.hln_parameters, data=self.data,
                                                first_baseline_i=self.first_baseline_i, exclude_covar=self.exclude_covar)
                self.ploidy_model.RunMCMC(self.n_iterations)
                copy_posteriors = self.ploidy_model.ReportMCMCData(self.burn_in, autocor_slice)
                loglike_diff = self.ploidy_model.LikelihoodComparison(norm_copy_num)

            # first choose more appropriate burn-in before increasing n_iterations again
            if loglike_diff < thresh_loglike_diff:
                peak_pos, peak_height = self.ploidy_model.DetectModeJump()
                # use significant peak, otherwise set back to default
                self.burn_in = peak_pos if peak_height > grad_threshold else int(round(self.burn_in_prop * self.n_iterations))
                logging.info('Setting burn-in to {} on run {}'.format(self.burn_in, tries))

                copy_posteriors = self.ploidy_model.ReportMCMCData(self.burn_in, autocor_slice)
                loglike_diff = self.ploidy_model.LikelihoodComparison(norm_copy_num)
            tries += 1
            # increase number of iterations with tries
            self.n_iterations += iter_step_size

        return copy_posteriors, loglike_diff

