import { create } from 'zustand';
import api from '@/lib/axios';

export interface HarborImage {
  id: string;
  name: string;
  tag: string;
  category: 'core' | 'custom' | 'user';
  protected: boolean;
  vulnerable?: boolean;
  [key: string]: any;
}

interface ImageStats {
  total: number;
  by_category: {
    core: number;
    custom: number;
    user: number;
  };
  protected: number;
  vulnerable: number;
}

interface ImageFilters {
  category: string | null;
  protected: boolean | null;
  search: string;
}

interface Pagination {
  skip: number;
  limit: number;
  total: number;
}

interface HarborJob {
  id: string;
  status: string;
  job_type: string;
  [key: string]: any;
}

interface HarborState {
  // State
  images: HarborImage[];
  jobs: HarborJob[];
  loading: boolean;
  error: string | null;
  stats: ImageStats;
  filters: ImageFilters;
  pagination: Pagination;

  // Computed getters
  getCoreImages: () => HarborImage[];
  getCustomImages: () => HarborImage[];
  getUserImages: () => HarborImage[];
  getProtectedImages: () => HarborImage[];

  // Actions - Images
  fetchImages: (options?: Partial<Pagination>) => Promise<any>;
  fetchImageStats: () => Promise<ImageStats>;
  getImage: (imageId: string) => Promise<HarborImage>;
  addImage: (imageData: Partial<HarborImage>) => Promise<any>;
  updateImage: (imageId: string, imageData: Partial<HarborImage>) => Promise<any>;
  deleteImage: (imageId: string) => Promise<any>;
  remirrorImage: (imageId: string) => Promise<any>;
  triggerMirror: (mirrorRequest: any) => Promise<any>;
  syncWithHarbor: () => Promise<any>;

  // Actions - Jobs
  fetchJobs: (options?: any) => Promise<any>;
  getJobStatus: (jobId: string) => Promise<HarborJob>;
  checkHarborHealth: () => Promise<any>;

  // Filter helpers
  setFilter: (key: keyof ImageFilters, value: any) => Promise<any>;
  clearFilters: () => Promise<any>;

  // Pagination helpers
  nextPage: () => Promise<any> | void;
  previousPage: () => Promise<any> | void;
  goToPage: (page: number) => Promise<any>;
}

export const useHarborStore = create<HarborState>((set, get) => ({
  // State
  images: [],
  jobs: [],
  loading: false,
  error: null,
  stats: {
    total: 0,
    by_category: {
      core: 0,
      custom: 0,
      user: 0
    },
    protected: 0,
    vulnerable: 0
  },
  filters: {
    category: null,
    protected: null,
    search: ''
  },
  pagination: {
    skip: 0,
    limit: 50,
    total: 0
  },

  // Computed getters
  getCoreImages: () => {
    const { images } = get();
    return images.filter(img => img.category === 'core');
  },

  getCustomImages: () => {
    const { images } = get();
    return images.filter(img => img.category === 'custom');
  },

  getUserImages: () => {
    const { images } = get();
    return images.filter(img => img.category === 'user');
  },

  getProtectedImages: () => {
    const { images } = get();
    return images.filter(img => img.protected);
  },

  // Actions - Images
  fetchImages: async (options = {}) => {
    set({ loading: true, error: null });

    try {
      const { pagination, filters } = get();
      const params: any = {
        skip: options.skip || pagination.skip,
        limit: options.limit || pagination.limit,
        ...filters
      };

      // Remove null/empty values
      Object.keys(params).forEach(key => {
        if (params[key] === null || params[key] === '') {
          delete params[key];
        }
      });

      const response = await api.get('/harbor/images', { params });

      set({
        images: response.data.images,
        pagination: {
          total: response.data.total,
          skip: response.data.skip,
          limit: response.data.limit
        },
        loading: false
      });

      return response.data;
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
      throw err;
    }
  },

  fetchImageStats: async () => {
    try {
      const response = await api.get('/harbor/stats/images');
      set({ stats: response.data });
      return response.data;
    } catch (err: any) {
      console.error('Failed to fetch image stats:', err);
      console.error('Error response:', err.response?.data);
      throw err;
    }
  },

  getImage: async (imageId: string) => {
    try {
      const response = await api.get(`/harbor/images/${imageId}`);
      return response.data;
    } catch (err: any) {
      set({ error: err.response?.data?.detail || err.message });
      throw err;
    }
  },

  addImage: async (imageData: Partial<HarborImage>) => {
    set({ loading: true, error: null });

    try {
      const response = await api.post('/harbor/images', imageData);

      // Refresh the list
      await get().fetchImages();

      set({ loading: false });
      return response.data;
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
      throw err;
    }
  },

  updateImage: async (imageId: string, imageData: Partial<HarborImage>) => {
    set({ loading: true, error: null });

    try {
      const response = await api.put(`/harbor/images/${imageId}`, imageData);

      // Update in local state
      set(state => ({
        images: state.images.map(img =>
          img.id === imageId ? response.data : img
        ),
        loading: false
      }));

      return response.data;
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
      throw err;
    }
  },

  deleteImage: async (imageId: string) => {
    set({ loading: true, error: null });

    try {
      const response = await api.delete(`/harbor/images/${imageId}`);

      // Remove from local state
      set(state => ({
        images: state.images.filter(img => img.id !== imageId),
        loading: false
      }));

      return response.data;
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
      throw err;
    }
  },

  remirrorImage: async (imageId: string) => {
    set({ loading: true, error: null });

    try {
      const response = await api.post(`/harbor/images/${imageId}/remirror`);
      set({ loading: false });
      return response.data;
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
      throw err;
    }
  },

  triggerMirror: async (mirrorRequest: any) => {
    set({ loading: true, error: null });

    try {
      const response = await api.post('/harbor/images/mirror', mirrorRequest);

      // Add jobs to local state
      if (response.data.jobs) {
        set(state => ({
          jobs: [...state.jobs, ...response.data.jobs],
          loading: false
        }));
      } else {
        set({ loading: false });
      }

      return response.data;
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
      throw err;
    }
  },

  syncWithHarbor: async () => {
    set({ loading: true, error: null });

    try {
      const response = await api.post('/harbor/images/sync', {});

      // Refresh the list
      await get().fetchImages();

      set({ loading: false });
      return response.data;
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
      throw err;
    }
  },

  // Actions - Jobs
  fetchJobs: async (options = {}) => {
    try {
      const params: any = {
        skip: options.skip || 0,
        limit: options.limit || 50,
        status: options.status,
        job_type: options.job_type
      };

      // Remove null values
      Object.keys(params).forEach(key => {
        if (params[key] === null || params[key] === undefined) {
          delete params[key];
        }
      });

      const response = await api.get('/harbor/jobs', { params });

      set({ jobs: response.data.jobs });
      return response.data;
    } catch (err: any) {
      set({ error: err.response?.data?.detail || err.message });
      throw err;
    }
  },

  getJobStatus: async (jobId: string) => {
    try {
      const response = await api.get(`/harbor/jobs/${jobId}`);

      // Update in local state
      set(state => ({
        jobs: state.jobs.map(job =>
          job.id === jobId ? response.data : job
        )
      }));

      return response.data;
    } catch (err: any) {
      set({ error: err.response?.data?.detail || err.message });
      throw err;
    }
  },

  checkHarborHealth: async () => {
    try {
      const response = await api.get('/harbor/health');
      return response.data;
    } catch (err: any) {
      console.error('Failed to check Harbor health:', err);
      return { status: 'unknown', error: err.message };
    }
  },

  // Filter helpers
  setFilter: (key: keyof ImageFilters, value: any) => {
    set(state => ({
      filters: {
        ...state.filters,
        [key]: value
      },
      pagination: {
        ...state.pagination,
        skip: 0 // Reset pagination when filtering
      }
    }));
    return get().fetchImages();
  },

  clearFilters: () => {
    set({
      filters: {
        category: null,
        protected: null,
        search: ''
      },
      pagination: {
        skip: 0,
        limit: 50,
        total: 0
      }
    });
    return get().fetchImages();
  },

  // Pagination helpers
  nextPage: () => {
    const { pagination } = get();
    if (pagination.skip + pagination.limit < pagination.total) {
      set(state => ({
        pagination: {
          ...state.pagination,
          skip: state.pagination.skip + state.pagination.limit
        }
      }));
      return get().fetchImages();
    }
  },

  previousPage: () => {
    const { pagination } = get();
    if (pagination.skip > 0) {
      set(state => ({
        pagination: {
          ...state.pagination,
          skip: Math.max(0, state.pagination.skip - state.pagination.limit)
        }
      }));
      return get().fetchImages();
    }
  },

  goToPage: (page: number) => {
    set(state => ({
      pagination: {
        ...state.pagination,
        skip: (page - 1) * state.pagination.limit
      }
    }));
    return get().fetchImages();
  }
}));
