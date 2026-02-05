<script lang="ts">
  import { RadioGroup as RadioGroupPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = RadioGroupPrimitive.RootProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<RadioGroupPrimitive.Root class={cn('grid gap-2', className)} {...restProps}>
  {@render children?.()}
</RadioGroupPrimitive.Root>
