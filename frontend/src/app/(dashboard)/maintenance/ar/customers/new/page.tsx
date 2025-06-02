'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import * as z from 'zod'
import { api } from '@/lib/api'
import Link from 'next/link'
import { ChevronLeft, Save } from 'lucide-react'

const customerSchema = z.object({
  customer_code: z.string()
    .min(1, "Customer code is required")
    .max(20, "Customer code must be 20 characters or less")
    .regex(/^[A-Z0-9-_]+$/, "Must be uppercase alphanumeric, dash, or underscore"),
  name: z.string().min(1, "Name is required"),
  payment_terms: z.number()
    .int()
    .min(0, "Payment terms cannot be negative")
    .max(365, "Payment terms cannot exceed 365 days"),
  credit_limit: z.string()
    .regex(/^\d+(\.\d{1,2})?$/, "Invalid amount format"),
  ar_account_id: z.number().optional().nullable(),
  address: z.object({
    street: z.string().optional(),
    city: z.string().optional(),
    state: z.string().optional(),
    postal_code: z.string().optional(),
    country: z.string().optional()
  }).optional(),
  contact_info: z.object({
    phone: z.string().optional(),
    email: z.string().email().optional().or(z.literal("")),
    contact_person: z.string().optional()
  }).optional()
})

type CustomerFormData = z.infer<typeof customerSchema>

interface GLAccount {
  id: number
  account_code: string
  account_name: string
}

export default function NewCustomerPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [arAccounts, setArAccounts] = useState<GLAccount[]>([])
  
  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
    watch
  } = useForm<CustomerFormData>({
    resolver: zodResolver(customerSchema),
    defaultValues: {
      payment_terms: 30,
      credit_limit: "0.00",
      address: {},
      contact_info: {}
    }
  })

  useEffect(() => {
    fetchARAccounts()
  }, [])

  const fetchARAccounts = async () => {
    try {
      const response = await api.get('/gl/accounts?account_type=ASSET')
      // Filter for typical AR accounts (you might want to add a specific flag)
      const arAccounts = response.data.filter((acc: GLAccount) => 
        acc.account_name.toLowerCase().includes('receivable') ||
        acc.account_code.startsWith('12') // Common AR account code prefix
      )
      setArAccounts(arAccounts)
    } catch (error) {
      console.error('Failed to fetch AR accounts:', error)
    }
  }

  const onSubmit = async (data: CustomerFormData) => {
    try {
      setLoading(true)
      await api.post('/customers', data)
      router.push('/customers')
    } catch (error: any) {
      console.error('Failed to create customer:', error)
      alert(error.response?.data?.detail || 'Failed to create customer')
    } finally {
      setLoading(false)
    }
  }

  // Transform customer code to uppercase as user types
  const customerCode = watch('customer_code')
  useEffect(() => {
    if (customerCode) {
      setValue('customer_code', customerCode.toUpperCase())
    }
  }, [customerCode, setValue])

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <Link
          href="/customers"
          className="inline-flex items-center text-gray-600 hover:text-gray-900"
        >
          <ChevronLeft className="h-4 w-4 mr-1" />
          Back to Customers
        </Link>
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <h1 className="text-2xl font-bold mb-6">New Customer</h1>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
          {/* Basic Information */}
          <div className="grid grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Customer Code *
              </label>
              <input
                type="text"
                {...register('customer_code')}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="CUST001"
              />
              {errors.customer_code && (
                <p className="mt-1 text-sm text-red-600">{errors.customer_code.message}</p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Customer Name *
              </label>
              <input
                type="text"
                {...register('name')}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Company Name Ltd"
              />
              {errors.name && (
                <p className="mt-1 text-sm text-red-600">{errors.name.message}</p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Payment Terms (Days) *
              </label>
              <input
                type="number"
                {...register('payment_terms', { valueAsNumber: true })}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {errors.payment_terms && (
                <p className="mt-1 text-sm text-red-600">{errors.payment_terms.message}</p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Credit Limit *
              </label>
              <input
                type="text"
                {...register('credit_limit')}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="0.00"
              />
              {errors.credit_limit && (
                <p className="mt-1 text-sm text-red-600">{errors.credit_limit.message}</p>
              )}
            </div>

            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                AR Control Account
              </label>
              <select
                {...register('ar_account_id', { 
                  setValueAs: (v) => v === '' ? null : parseInt(v) 
                })}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">-- Use Default --</option>
                {arAccounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.account_code} - {account.account_name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Address Information */}
          <div>
            <h3 className="text-lg font-medium mb-4">Address Information</h3>
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Street Address
                </label>
                <input
                  type="text"
                  {...register('address.street')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  City
                </label>
                <input
                  type="text"
                  {...register('address.city')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  State/Province
                </label>
                <input
                  type="text"
                  {...register('address.state')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Postal Code
                </label>
                <input
                  type="text"
                  {...register('address.postal_code')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Country
                </label>
                <input
                  type="text"
                  {...register('address.country')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Contact Information */}
          <div>
            <h3 className="text-lg font-medium mb-4">Contact Information</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Phone
                </label>
                <input
                  type="text"
                  {...register('contact_info.phone')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Email
                </label>
                <input
                  type="email"
                  {...register('contact_info.email')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Contact Person
                </label>
                <input
                  type="text"
                  {...register('contact_info.contact_person')}
                  className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex justify-end space-x-4">
            <Link
              href="/customers"
              className="px-4 py-2 border border-gray-300 rounded-md hover:bg-gray-50"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 flex items-center"
            >
              <Save className="h-4 w-4 mr-2" />
              {loading ? 'Creating...' : 'Create Customer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
} 