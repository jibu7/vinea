'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import * as z from 'zod'
import { api } from '@/lib/api'
import Link from 'next/link'
import { ChevronLeft, Save } from 'lucide-react'

const supplierSchema = z.object({
  supplier_code: z.string()
    .min(1, "Supplier code is required")
    .max(20, "Supplier code must be 20 characters or less")
    .regex(/^[A-Z0-9-_]+$/, "Must be uppercase alphanumeric, dash, or underscore"),
  name: z.string().min(1, "Name is required"),
  payment_terms: z.number()
    .int()
    .min(0, "Payment terms cannot be negative")
    .max(365, "Payment terms cannot exceed 365 days"),
  ap_account_id: z.number().optional().nullable(),
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

type SupplierFormData = z.infer<typeof supplierSchema>

interface GLAccount {
  id: number
  account_code: string
  account_name: string
}

export default function NewSupplierPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [apAccounts, setApAccounts] = useState<GLAccount[]>([])
  
  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
    watch
  } = useForm<SupplierFormData>({
    resolver: zodResolver(supplierSchema),
    defaultValues: {
      payment_terms: 30,
      address: {},
      contact_info: {}
    }
  })

  useEffect(() => {
    fetchAPAccounts()
  }, [])

  const fetchAPAccounts = async () => {
    try {
      const response = await api.get('/gl/accounts?account_type=LIABILITY')
      // Filter for typical AP accounts
      const apAccounts = response.data.filter((acc: GLAccount) => 
        acc.account_name.toLowerCase().includes('payable') ||
        acc.account_code.startsWith('21') // Common AP account code prefix
      )
      setApAccounts(apAccounts)
    } catch (error) {
      console.error('Failed to fetch AP accounts:', error)
    }
  }

  const onSubmit = async (data: SupplierFormData) => {
    try {
      setLoading(true)
      await api.post('/suppliers', data)
      router.push('/suppliers')
    } catch (error: any) {
      console.error('Failed to create supplier:', error)
      alert(error.response?.data?.detail || 'Failed to create supplier')
    } finally {
      setLoading(false)
    }
  }

  // Transform supplier code to uppercase as user types
  const supplierCode = watch('supplier_code')
  useEffect(() => {
    if (supplierCode) {
      setValue('supplier_code', supplierCode.toUpperCase())
    }
  }, [supplierCode, setValue])

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <Link
          href="/suppliers"
          className="inline-flex items-center text-gray-600 hover:text-gray-900"
        >
          <ChevronLeft className="h-4 w-4 mr-1" />
          Back to Suppliers
        </Link>
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <h1 className="text-2xl font-bold mb-6">New Supplier</h1>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
          {/* Basic Information */}
          <div className="grid grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Supplier Code *
              </label>
              <input
                type="text"
                {...register('supplier_code')}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="SUPP001"
              />
              {errors.supplier_code && (
                <p className="mt-1 text-sm text-red-600">{errors.supplier_code.message}</p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Supplier Name *
              </label>
              <input
                type="text"
                {...register('name')}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Supplier Company Ltd"
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
                AP Control Account
              </label>
              <select
                {...register('ap_account_id', { 
                  setValueAs: (v) => v === '' ? null : parseInt(v) 
                })}
                className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">-- Use Default --</option>
                {apAccounts.map((account) => (
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
              href="/suppliers"
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
              {loading ? 'Creating...' : 'Create Supplier'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
} 