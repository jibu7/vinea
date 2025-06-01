'use client';

import { useState, useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { X, AlertCircle } from 'lucide-react';
import { useApi } from '@/hooks/useApi';

const periodSchema = z.object({
  period_name: z.string().min(1, 'Period name is required'),
  start_date: z.string().min(1, 'Start date is required'),
  end_date: z.string().min(1, 'End date is required'),
  financial_year: z.number().min(1900).max(2100),
}).refine((data) => {
  const start = new Date(data.start_date);
  const end = new Date(data.end_date);
  return end > start;
}, {
  message: "End date must be after start date",
  path: ["end_date"],
});

type PeriodFormData = z.infer<typeof periodSchema>;

interface AccountingPeriodFormProps {
  period?: any;
  onClose: () => void;
  onSuccess: () => void;
}

export function AccountingPeriodForm({ period, onClose, onSuccess }: AccountingPeriodFormProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [validationResult, setValidationResult] = useState<any>(null);
  const api = useApi();

  const {
    register,
    handleSubmit,
    formState: { errors },
    watch,
    setValue,
  } = useForm<PeriodFormData>({
    resolver: zodResolver(periodSchema),
    defaultValues: {
      period_name: period?.period_name || '',
      start_date: period?.start_date || '',
      end_date: period?.end_date || '',
      financial_year: period?.financial_year || new Date().getFullYear(),
    },
  });

  const watchedData = watch();

  // Validate period on form value changes
  useEffect(() => {
    if (watchedData.start_date && watchedData.end_date && !period) {
      validatePeriod();
    }
  }, [watchedData.start_date, watchedData.end_date]);

  const validatePeriod = async () => {
    try {
      const response = await api.post('/api/accounting-periods/validate', {
        ...watchedData,
        company_id: null, // Will be set by backend
      });
      setValidationResult(response.data);
    } catch (error) {
      console.error('Failed to validate period:', error);
    }
  };

  const onSubmit = async (data: PeriodFormData) => {
    try {
      setIsLoading(true);
      
      if (period) {
        // Update existing period (only name and closed status can be updated)
        await api.put(`/api/accounting-periods/${period.id}`, {
          period_name: data.period_name,
        });
      } else {
        // Create new period
        await api.post('/api/accounting-periods', data);
      }
      
      onSuccess();
    } catch (error: any) {
      console.error('Failed to save period:', error);
      alert(error.response?.data?.detail || 'Failed to save period');
    } finally {
      setIsLoading(false);
    }
  };

  // Auto-generate period name based on dates
  const generatePeriodName = () => {
    const startDate = watch('start_date');
    const endDate = watch('end_date');
    
    if (startDate && endDate) {
      const start = new Date(startDate);
      const end = new Date(endDate);
      const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      
      const startMonth = monthNames[start.getMonth()];
      const endMonth = monthNames[end.getMonth()];
      const year = start.getFullYear();
      
      if (start.getMonth() === end.getMonth()) {
        setValue('period_name', `${startMonth} ${year}`);
      } else {
        setValue('period_name', `${startMonth} - ${endMonth} ${year}`);
      }
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-semibold">
            {period ? 'Edit Accounting Period' : 'Create Accounting Period'}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Period Name
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                {...register('period_name')}
                className="flex-1 px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {!period && (
                <button
                  type="button"
                  onClick={generatePeriodName}
                  className="px-3 py-2 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200"
                >
                  Auto
                </button>
              )}
            </div>
            {errors.period_name && (
              <p className="mt-1 text-sm text-red-600">{errors.period_name.message}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Financial Year
            </label>
            <input
              type="number"
              {...register('financial_year', { valueAsNumber: true })}
              disabled={!!period}
              className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
            />
            {errors.financial_year && (
              <p className="mt-1 text-sm text-red-600">{errors.financial_year.message}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Start Date
              </label>
              <input
                type="date"
                {...register('start_date')}
                disabled={!!period}
                className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
              />
              {errors.start_date && (
                <p className="mt-1 text-sm text-red-600">{errors.start_date.message}</p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                End Date
              </label>
              <input
                type="date"
                {...register('end_date')}
                disabled={!!period}
                className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
              />
              {errors.end_date && (
                <p className="mt-1 text-sm text-red-600">{errors.end_date.message}</p>
              )}
            </div>
          </div>

          {/* Validation Results */}
          {validationResult && !validationResult.is_valid && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <div className="flex items-start">
                <AlertCircle className="h-5 w-5 text-red-400 mt-0.5 mr-2" />
                <div className="text-sm text-red-700">
                  <p className="font-medium">{validationResult.message}</p>
                  {validationResult.overlapping_periods?.length > 0 && (
                    <ul className="mt-1 list-disc list-inside">
                      {validationResult.overlapping_periods.map((p: any) => (
                        <li key={p.id}>
                          {p.period_name} ({new Date(p.start_date).toLocaleDateString()} - {new Date(p.end_date).toLocaleDateString()})
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </div>
          )}

          {period && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
              <p className="text-sm text-blue-700">
                <strong>Note:</strong> Only the period name can be edited. To change dates, create a new period.
              </p>
            </div>
          )}

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-gray-700 bg-gray-200 rounded-lg hover:bg-gray-300"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading || (validationResult && !validationResult.is_valid)}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {isLoading ? 'Saving...' : period ? 'Update' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
} 